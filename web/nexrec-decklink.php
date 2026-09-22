<?php
/**
 * DeckLink input checks, ffmpeg device-list parsing, and status-helper JSON.
 * No shell. The status binary is invoked with a fixed argv.
 */
declare(strict_types=1);

function nexrec_decklink_device_ok(string $name): bool {
    if ($name === '' || strlen($name) > 80) {
        return false;
    }
    if (preg_match('/^[0-9]{1,2}$/', $name)) {
        return true;
    }
    return (bool) preg_match('/^[A-Za-z0-9][A-Za-z0-9 ._()\/+\-]{0,78}$/', $name);
}

function nexrec_decklink_format_ok(string $code): bool {
    if ($code === '') {
        return true;
    }
    return (bool) preg_match('/^[A-Za-z0-9]{2,8}$/', $code);
}

/** @return string empty when the input is acceptable */
function nexrec_decklink_input_error(string $type, string $device, string $format): string {
    if ($type !== 'decklink') {
        return '';
    }
    if (!nexrec_decklink_device_ok($device)) {
        return 'decklink_device must be a name like "DeckLink Quad 2 (1)" or a sub-device index';
    }
    if (!nexrec_decklink_format_ok($format)) {
        return 'invalid decklink format_code';
    }
    return '';
}

function nexrec_decklink_bin_ok(string $path): bool {
    if ($path === '' || !str_starts_with($path, '/') || str_contains($path, '..')) {
        return false;
    }
    if (preg_match('/[\s;|&$`\n\r]/', $path)) {
        return false;
    }
    return is_file($path) && is_executable($path);
}

function nexrec_decklink_status_bin(): string {
    $explicit = '';
    if (function_exists('nexrec_setting')) {
        $explicit = (string) nexrec_setting('ffmpeg.decklink_status_bin');
    }
    if ($explicit === '') {
        $env = getenv('NEXREC_DECKLINK_STATUS_BIN');
        if (is_string($env)) {
            $explicit = trim($env);
        }
    }
    if ($explicit !== '') {
        return nexrec_decklink_bin_ok($explicit) ? $explicit : '';
    }
    $candidates = [
        '/usr/local/bin/nexrec-decklink-status',
        '/usr/bin/nexrec-decklink-status',
    ];
    $root = getenv('NEXREC_APP_ROOT');
    if (!is_string($root) || $root === '') {
        $root = dirname(__DIR__);
    }
    $candidates[] = rtrim($root, '/') . '/tools/decklink-status/nexrec-decklink-status';
    foreach ($candidates as $path) {
        if (nexrec_decklink_bin_ok($path)) {
            return $path;
        }
    }
    return '';
}

/** @return array{rc:int,out:string,err:string} */
function nexrec_decklink_run(array $cmd, int $timeoutSec = 8): array {
    $desc = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $proc = proc_open($cmd, $desc, $pipes, null, null, ['bypass_shell' => true]);
    if (!is_resource($proc)) {
        return ['rc' => 1, 'out' => '', 'err' => 'proc_open failed'];
    }
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $out = '';
    $err = '';
    $start = microtime(true);
    $timedOut = false;
    while (true) {
        $st = proc_get_status($proc);
        $out .= (string) stream_get_contents($pipes[1]);
        $err .= (string) stream_get_contents($pipes[2]);
        if (!$st['running']) {
            break;
        }
        if ((microtime(true) - $start) > $timeoutSec) {
            proc_terminate($proc, 9);
            $timedOut = true;
            $err .= "\ntimeout";
            break;
        }
        usleep(40000);
    }
    $out .= (string) stream_get_contents($pipes[1]);
    $err .= (string) stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $rc = proc_close($proc);
    if ($timedOut) {
        $rc = 124;
    }
    if (strlen($out) > 65536) {
        $out = substr($out, 0, 65536);
    }
    if (strlen($err) > 65536) {
        $err = substr($err, -65536);
    }
    return ['rc' => $rc, 'out' => $out, 'err' => $err];
}

/** @return list<array{index:int,name:string}> */
function nexrec_decklink_parse_devices(string $text): array {
    $devices = [];
    $header = false;
    foreach (preg_split('/\r?\n/', $text) ?: [] as $line) {
        if (str_contains($line, 'DeckLink') && stripos($line, 'devices') !== false) {
            $header = true;
            continue;
        }
        if (!$header) {
            continue;
        }
        if (str_contains($line, 'Immediate exit') || str_starts_with(trim($line), 'dummy:')) {
            break;
        }
        if (!preg_match("/['\"]([^'\"]+)['\"]/", $line, $m)) {
            continue;
        }
        $name = trim($m[1]);
        if ($name === '' || strcasecmp($name, 'dummy') === 0) {
            continue;
        }
        $devices[] = ['index' => count($devices), 'name' => $name];
    }
    return $devices;
}

/** @return list<string> */
function nexrec_decklink_parse_formats(string $text): array {
    $codes = [];
    foreach (preg_split('/\r?\n/', $text) ?: [] as $line) {
        if (str_contains($line, 'Supported formats')) {
            continue;
        }
        if (preg_match("/'([A-Za-z0-9]{2,8})'/", $line, $m)) {
            if (!in_array($m[1], $codes, true)) {
                $codes[] = $m[1];
            }
        }
    }
    return $codes;
}

function nexrec_decklink_ffmpeg_enabled(string $text): bool {
    $low = strtolower($text);
    if (str_contains($low, 'unknown input format') && str_contains($low, 'decklink')) {
        return false;
    }
    if (str_contains($low, 'unrecognized option') && str_contains($low, 'list_devices')) {
        return false;
    }
    return str_contains($low, 'decklink');
}

/**
 * @param list<array<string,mixed>> $devices
 * @return array<string,mixed>|null
 */
function nexrec_decklink_match(string $spec, array $devices): ?array {
    $raw = trim($spec);
    if (preg_match('/^[0-9]{1,2}$/', $raw)) {
        $idx = (int) $raw;
        foreach ($devices as $dev) {
            if ((int) ($dev['index'] ?? -1) === $idx) {
                return $dev;
            }
        }
    }
    foreach ($devices as $dev) {
        if ((string) ($dev['name'] ?? '') === $raw) {
            return $dev;
        }
    }
    $low = strtolower($raw);
    foreach ($devices as $dev) {
        if (strtolower((string) ($dev['name'] ?? '')) === $low) {
            return $dev;
        }
    }
    return null;
}

/** @return array<string,mixed> */
function nexrec_decklink_signal_from_json(string $text, string $device): array {
    $unknown = [
        'signal' => 'unknown',
        'sdi_lock' => null,
        'format' => '',
        'detail' => 'unknown — needs DeckLink tools',
        'probe' => 'unavailable',
        'busy' => null,
        'reference_locked' => null,
        'reference_mode' => '',
    ];
    $data = json_decode($text, true);
    if (!is_array($data)) {
        $unknown['detail'] = 'status helper did not return JSON';
        return $unknown;
    }
    $devices = $data['devices'] ?? [];
    if (!is_array($devices)) {
        $devices = [];
    }
    $err = $data['error'] ?? null;
    if ($err && $devices === []) {
        $unknown['detail'] = $err === 'no_decklink_api'
            ? 'DeckLink drivers are not available'
            : substr((string) $err, 0, 200);
        return $unknown;
    }
    $match = nexrec_decklink_match($device, $devices);
    if ($match === null) {
        return [
            'signal' => 'unknown',
            'sdi_lock' => null,
            'format' => '',
            'detail' => 'status helper did not report this sub-device',
            'probe' => 'tool',
            'busy' => null,
            'reference_locked' => null,
            'reference_mode' => '',
        ];
    }
    $locked = !empty($match['input_locked']);
    $mode = (string) ($match['input_mode'] ?? '');
    if (strcasecmp($mode, 'unknown') === 0) {
        $mode = '';
    }
    $busy = !empty($match['busy']);
    $refLocked = !empty($match['reference_locked']);
    $refMode = (string) ($match['reference_mode'] ?? '');
    if (strcasecmp($refMode, 'unknown') === 0) {
        $refMode = '';
    }
    $bits = [];
    if ($busy) {
        $bits[] = 'sub-device busy (record process holds it); lock read from DeckLink status';
    }
    if ($refLocked && $refMode !== '') {
        $bits[] = 'reference ' . $refMode;
    }
    return [
        'signal' => $locked ? 'present' : 'no_signal',
        'sdi_lock' => $locked ? 1 : 0,
        'format' => $locked ? $mode : '',
        'detail' => implode('; ', $bits),
        'probe' => 'tool',
        'busy' => $busy ? 1 : 0,
        'reference_locked' => $refLocked ? 1 : 0,
        'reference_mode' => $refMode,
    ];
}

function nexrec_decklink_ffmpeg_bin(): string {
    $ffmpeg = '';
    if (function_exists('nexrec_setting')) {
        $ffmpeg = (string) nexrec_setting('ffmpeg.path');
    }
    if ($ffmpeg === '' || !is_executable($ffmpeg)) {
        $env = getenv('NEXREC_FFMPEG');
        if (is_string($env) && $env !== '' && is_executable($env)) {
            $ffmpeg = $env;
        }
    }
    if ($ffmpeg === '' || !is_executable($ffmpeg)) {
        foreach (['/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg'] as $p) {
            if (is_executable($p)) {
                return $p;
            }
        }
        return 'ffmpeg';
    }
    return $ffmpeg;
}

/** @return array{decklink_enabled:bool,devices:list<array{index:int,name:string}>,message:string} */
function nexrec_decklink_query_devices(): array {
    $ffmpeg = nexrec_decklink_ffmpeg_bin();
    if (!is_executable($ffmpeg) && $ffmpeg !== 'ffmpeg') {
        return ['decklink_enabled' => false, 'devices' => [], 'message' => 'ffmpeg not found'];
    }
    $ran = nexrec_decklink_run([$ffmpeg, '-hide_banner', '-f', 'decklink', '-list_devices', '1', '-i', 'dummy'], 8);
    $blob = $ran['out'] . "\n" . $ran['err'];
    if (!nexrec_decklink_ffmpeg_enabled($blob)) {
        return [
            'decklink_enabled' => false,
            'devices' => [],
            'message' => 'This ffmpeg has no DeckLink input. Rebuild with --enable-decklink and the Blackmagic SDK headers.',
        ];
    }
    $devices = nexrec_decklink_parse_devices($blob);
    $msg = $devices === [] ? 'ffmpeg is decklink-enabled but no sub-devices were listed' : '';
    return ['decklink_enabled' => true, 'devices' => $devices, 'message' => $msg];
}

/** @return array{decklink_enabled:bool,formats:list<string>,message:string} */
function nexrec_decklink_query_formats(string $device): array {
    if (!nexrec_decklink_device_ok($device) || preg_match('/^[0-9]{1,2}$/', $device)) {
        return ['decklink_enabled' => false, 'formats' => [], 'message' => 'format list needs a DeckLink display name'];
    }
    $ffmpeg = nexrec_decklink_ffmpeg_bin();
    $ran = nexrec_decklink_run(
        [$ffmpeg, '-hide_banner', '-f', 'decklink', '-list_formats', '1', '-i', $device],
        8
    );
    $blob = $ran['out'] . "\n" . $ran['err'];
    if (!nexrec_decklink_ffmpeg_enabled($blob) && !str_contains($blob, 'Supported formats')) {
        return [
            'decklink_enabled' => false,
            'formats' => [],
            'message' => 'This ffmpeg has no DeckLink input. Rebuild with --enable-decklink.',
        ];
    }
    return [
        'decklink_enabled' => true,
        'formats' => nexrec_decklink_parse_formats($blob),
        'message' => '',
    ];
}

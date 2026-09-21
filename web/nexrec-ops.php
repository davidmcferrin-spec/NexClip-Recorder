<?php
/**
 * Ops console: unit status, journal, allowlisted start/stop, signal rows.
 * Control goes through bin/nexrec-systemctl.sh (sudo -n). No shell interpolation.
 */
declare(strict_types=1);

const NEXREC_OPS_VERBS = ['start', 'stop', 'restart', 'enable', 'disable', 'is-active', 'is-enabled', 'show', 'journal'];
const NEXREC_OPS_CONTROL_VERBS = ['start', 'stop', 'restart', 'enable', 'disable'];

function nexrec_ops_unit_allowed(string $unit): bool {
    return (bool) preg_match(
        '/^(?:mediamtx\.service|nexrec-export\.service|nexrec-cleanup\.(?:service|timer)|nexrec-analyze\.service|nexrec-nexclip\.(?:service|timer)|nexrec-(?:record|preview)@[a-z0-9][a-z0-9-]{0,31}\.service)$/',
        $unit
    );
}

function nexrec_ops_verb_allowed(string $verb): bool {
    return in_array($verb, NEXREC_OPS_VERBS, true);
}

function nexrec_ops_fixed_units(): array {
    return [
        'mediamtx.service',
        'nexrec-export.service',
        'nexrec-cleanup.service',
        'nexrec-cleanup.timer',
        'nexrec-analyze.service',
        'nexrec-nexclip.service',
        'nexrec-nexclip.timer',
    ];
}

/** @param list<string> $inputIds */
function nexrec_ops_unit_names(array $inputIds): array {
    $units = nexrec_ops_fixed_units();
    foreach ($inputIds as $id) {
        $id = strtolower(trim((string) $id));
        if (!preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $id)) {
            continue;
        }
        $units[] = 'nexrec-record@' . $id . '.service';
        $units[] = 'nexrec-preview@' . $id . '.service';
    }
    return $units;
}

function nexrec_ops_wrapper_path(): string {
    $env = getenv('NEXREC_SYSTEMCTL_WRAPPER');
    if (is_string($env) && $env !== '') {
        return $env;
    }
    return dirname(__DIR__) . '/bin/nexrec-systemctl.sh';
}

function nexrec_ops_systemctl_bin(): ?string {
    $env = getenv('NEXREC_SYSTEMCTL_BIN');
    if (is_string($env) && $env !== '' && is_executable($env)) {
        return $env;
    }
    foreach (['/bin/systemctl', '/usr/bin/systemctl'] as $p) {
        if (is_executable($p)) {
            return $p;
        }
    }
    return null;
}

function nexrec_ops_proc(array $cmd): array {
    $desc = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $proc = proc_open($cmd, $desc, $pipes, null, null, ['bypass_shell' => true]);
    if (!is_resource($proc)) {
        return ['rc' => 1, 'out' => '', 'err' => 'proc_open failed'];
    }
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $rc = proc_close($proc);
    $out = is_string($out) ? $out : '';
    $err = is_string($err) ? $err : '';
    if (strlen($out) > 65536) {
        $out = substr($out, -65536);
    }
    if (strlen($err) > 8192) {
        $err = substr($err, -8192);
    }
    return ['rc' => $rc, 'out' => $out, 'err' => $err];
}

function nexrec_ops_use_sudo(): bool {
    $skip = getenv('NEXREC_OPS_NO_SUDO');
    if (is_string($skip) && in_array(strtolower($skip), ['1', 'true', 'yes', 'on'], true)) {
        return false;
    }
    return is_executable('/usr/bin/sudo');
}

/** @return array{rc:int,out:string,err:string,via:string} */
function nexrec_ops_wrapper(string $verb, string $unit, ?int $lines = null): array {
    if (!nexrec_ops_verb_allowed($verb)) {
        throw new InvalidArgumentException('verb not allowed');
    }
    if (!nexrec_ops_unit_allowed($unit)) {
        throw new InvalidArgumentException('unit not allowed');
    }
    $wrapper = nexrec_ops_wrapper_path();
    if (!is_file($wrapper)) {
        return ['rc' => 127, 'out' => '', 'err' => 'helper missing', 'via' => 'missing'];
    }
    $args = [$verb, $unit];
    if ($verb === 'journal') {
        $n = $lines ?? 80;
        if ($n < 1) {
            $n = 1;
        }
        if ($n > 200) {
            $n = 200;
        }
        $args[] = (string) $n;
    }
    $via = 'direct';
    if (nexrec_ops_use_sudo()) {
        $cmd = array_merge(['/usr/bin/sudo', '-n', $wrapper], $args);
        $via = 'sudo';
    } else {
        $cmd = array_merge([$wrapper], $args);
    }
    $ran = nexrec_ops_proc($cmd);
    $ran['via'] = $via;
    return $ran;
}

function nexrec_ops_parse_show(string $text): array {
    $props = [];
    foreach (preg_split('/\r?\n/', $text) ?: [] as $line) {
        $eq = strpos($line, '=');
        if ($eq === false) {
            continue;
        }
        $props[substr($line, 0, $eq)] = substr($line, $eq + 1);
    }
    $active = strtolower(trim((string) ($props['ActiveState'] ?? '')));
    if (!in_array($active, ['active', 'inactive', 'failed', 'activating', 'deactivating'], true)) {
        $active = $active === '' ? 'unknown' : $active;
    }
    $enabled = trim((string) ($props['UnitFileState'] ?? ''));
    if ($enabled === '') {
        $enabled = 'unknown';
    }
    $enter = trim((string) ($props['ActiveEnterTimestamp'] ?? ''));
    $uptime = null;
    if ($active === 'active' && $enter !== '' && $enter !== 'n/a') {
        $ts = strtotime($enter);
        if ($ts !== false && $ts > 0) {
            $uptime = max(0, time() - $ts);
        }
    }
    return [
        'active' => $active,
        'enabled' => $enabled,
        'sub' => trim((string) ($props['SubState'] ?? '')),
        'active_enter' => ($enter === '' || $enter === 'n/a') ? '' : $enter,
        'uptime_s' => $uptime,
        'load' => trim((string) ($props['LoadState'] ?? '')),
    ];
}

function nexrec_ops_query_unit(string $unit): array {
    if (!nexrec_ops_unit_allowed($unit)) {
        throw new InvalidArgumentException('unit not allowed');
    }
    $base = [
        'unit' => $unit,
        'active' => 'unknown',
        'enabled' => 'unknown',
        'sub' => '',
        'active_enter' => '',
        'uptime_s' => null,
        'load' => '',
        'group' => str_contains($unit, '@') ? 'input' : 'core',
    ];
    $bin = nexrec_ops_systemctl_bin();
    if ($bin === null) {
        return $base;
    }
    $ran = nexrec_ops_proc([
        $bin, 'show', $unit,
        '-p', 'Id', '-p', 'ActiveState', '-p', 'SubState', '-p', 'UnitFileState',
        '-p', 'ActiveEnterTimestamp', '-p', 'LoadState',
        '--no-pager',
    ]);
    if ($ran['out'] === '' && $ran['rc'] !== 0) {
        return $base;
    }
    return array_merge($base, nexrec_ops_parse_show($ran['out']));
}

function nexrec_ops_enabled_input_ids(): array {
    $ids = [];
    $res = nexrec_db()->query('SELECT id FROM inputs WHERE enabled=1 ORDER BY id');
    if ($res !== false) {
        while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
            $ids[] = (string) $row['id'];
        }
    }
    return $ids;
}

function nexrec_ops_units_payload(): array {
    $out = [];
    foreach (nexrec_ops_unit_names(nexrec_ops_enabled_input_ids()) as $unit) {
        $out[] = nexrec_ops_query_unit($unit);
    }
    return $out;
}

function nexrec_ops_journal(string $unit, int $n = 80): array {
    $ran = nexrec_ops_wrapper('journal', $unit, $n);
    $text = trim($ran['out']);
    if ($text === '' && $ran['err'] !== '') {
        $text = trim($ran['err']);
    }
    return [
        'unit' => $unit,
        'lines' => $text === '' ? [] : preg_split('/\r?\n/', $text),
        'rc' => $ran['rc'],
        'via' => $ran['via'],
    ];
}

function nexrec_ops_control(string $verb, string $unit): array {
    if (!in_array($verb, NEXREC_OPS_CONTROL_VERBS, true)) {
        throw new InvalidArgumentException('verb not allowed');
    }
    $ran = nexrec_ops_wrapper($verb, $unit, null);
    $ok = $ran['rc'] === 0;
    $err = trim($ran['err']);
    if (!$ok && $err === '') {
        $err = trim($ran['out']);
    }
    if (!$ok && ($ran['via'] ?? '') === 'sudo' && (str_contains($err, 'password') || str_contains($err, 'not allowed') || str_contains($err, 'a password is required'))) {
        $err = 'sudo helper unavailable (install the nexrec-systemctl sudoers drop-in; see README)';
    }
    return [
        'ok' => $ok,
        'verb' => $verb,
        'unit' => $unit,
        'rc' => $ran['rc'],
        'via' => $ran['via'],
        'output' => trim($ran['out']),
        'error' => $ok ? '' : $err,
    ];
}

function nexrec_ops_parse_kv_status(string $text): array {
    $signal = '';
    $lock = null;
    $fmt = '';
    foreach (preg_split('/\r?\n|,/', $text) ?: [] as $line) {
        $line = trim($line);
        if ($line === '' || !str_contains($line, '=')) {
            continue;
        }
        [$k, $v] = explode('=', $line, 2);
        $k = strtolower(trim($k));
        $v = trim($v);
        if ($k === 'signal' || $k === 'state') {
            $signal = strtolower($v);
        } elseif (in_array($k, ['lock', 'sdi_lock', 'locked'], true)) {
            $lock = in_array(strtolower($v), ['1', 'true', 'yes', 'locked', 'present'], true) ? 1 : 0;
        } elseif (in_array($k, ['format', 'format_code', 'video_format'], true)) {
            $fmt = $v;
        }
    }
    if (!in_array($signal, ['present', 'no_signal', 'unknown'], true)) {
        if ($lock === 1) {
            $signal = 'present';
        } elseif ($lock === 0) {
            $signal = 'no_signal';
        } else {
            $signal = 'unknown';
        }
    }
    return ['signal' => $signal, 'sdi_lock' => $lock, 'format' => substr($fmt, 0, 80), 'probe' => 'tool', 'detail' => ''];
}

function nexrec_ops_decklink_probe(string $device): array {
    $unknown = [
        'signal' => 'unknown',
        'sdi_lock' => null,
        'format' => '',
        'detail' => 'unknown — needs DeckLink tools',
        'probe' => 'unavailable',
    ];
    if ($device === '') {
        return $unknown;
    }
    $bin = getenv('NEXREC_DECKLINK_STATUS_BIN');
    if (is_string($bin) && $bin !== '' && str_starts_with($bin, '/') && is_executable($bin)
        && !preg_match('/[\s;|&$`]/', $bin)) {
        $ran = nexrec_ops_proc([$bin, $device]);
        $parsed = nexrec_ops_parse_kv_status($ran['out'] . "\n" . $ran['err']);
        if ($parsed['signal'] === 'unknown' && $parsed['format'] === '' && $ran['rc'] !== 0) {
            return $unknown;
        }
        return $parsed;
    }
    $ffmpeg = getenv('NEXREC_FFMPEG');
    if (!is_string($ffmpeg) || $ffmpeg === '' || !is_executable($ffmpeg)) {
        foreach (['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg'] as $p) {
            if (is_executable($p)) {
                $ffmpeg = $p;
                break;
            }
        }
    }
    if (is_string($ffmpeg) && is_executable($ffmpeg)) {
        $ran = nexrec_ops_proc([$ffmpeg, '-hide_banner', '-f', 'decklink', '-list_devices', '1', '-i', 'dummy']);
        $blob = $ran['out'] . "\n" . $ran['err'];
        if ($blob !== '' && str_contains($blob, $device)) {
            $unknown['detail'] = 'unknown — needs DeckLink tools (ffmpeg lists this device, but that is not SDI lock)';
            $unknown['probe'] = 'listed';
        }
    }
    return $unknown;
}

function nexrec_ops_classify_ip(?string $seenAt, ?string $lastChunkAt, int $segmentS): string {
    $window = max($segmentS * 2, 30);
    $now = time();
    $seen = $seenAt ? strtotime($seenAt) : false;
    if ($seen === false || ($now - $seen) > 20) {
        return 'down';
    }
    $chunk = $lastChunkAt ? strtotime($lastChunkAt) : false;
    if ($chunk === false) {
        return ($now - $seen) < $window ? 'receiving' : 'stalled';
    }
    if (($now - $chunk) <= $window) {
        return 'receiving';
    }
    return 'stalled';
}

function nexrec_ops_signals(): array {
    $segment = 300;
    if (function_exists('nexrec_setting_int')) {
        $segment = nexrec_setting_int('ffmpeg.segment_seconds', 300);
    }
    $inputs = [];
    $res = nexrec_db()->query('SELECT id, name, source_type, decklink_device, decklink_format, enabled FROM inputs ORDER BY name');
    if ($res !== false) {
        while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
            $inputs[] = $row;
        }
    }
    $heart = [];
    $hres = nexrec_db()->query('SELECT * FROM input_heartbeats');
    if ($hres !== false) {
        while ($row = $hres->fetchArray(SQLITE3_ASSOC)) {
            $heart[(string) $row['input_id']] = $row;
        }
    }
    $out = [];
    foreach ($inputs as $inp) {
        $id = (string) $inp['id'];
        $type = strtolower((string) ($inp['source_type'] ?? ''));
        $hb = $heart[$id] ?? null;
        $lastChunk = $hb['last_chunk_at'] ?? null;
        if (!$lastChunk) {
            $st = nexrec_db()->prepare('SELECT start_at FROM chunks WHERE input_id=:i ORDER BY start_at DESC LIMIT 1');
            $st->bindValue(':i', $id, SQLITE3_TEXT);
            $crow = $st->execute()->fetchArray(SQLITE3_ASSOC);
            if ($crow) {
                $lastChunk = (string) $crow['start_at'];
            }
        }
        $seen = $hb['seen_at'] ?? '';
        $row = [
            'input_id' => $id,
            'name' => (string) ($inp['name'] ?? $id),
            'source_type' => $type,
            'enabled' => (int) ($inp['enabled'] ?? 0),
            'signal' => 'unknown',
            'sdi_lock' => null,
            'format' => (string) ($inp['decklink_format'] ?? ''),
            'detail' => '',
            'last_chunk_at' => $lastChunk,
            'seen_at' => $seen,
            'transport' => 'other',
        ];
        if ($type === 'decklink') {
            $row['transport'] = 'sdi';
            $probe = nexrec_ops_decklink_probe((string) ($inp['decklink_device'] ?? ''));
            if (($probe['probe'] ?? '') === 'tool') {
                $row['signal'] = $probe['signal'];
                $row['sdi_lock'] = $probe['sdi_lock'];
                if ($probe['format'] !== '') {
                    $row['format'] = $probe['format'];
                }
                $row['detail'] = (string) ($probe['detail'] ?? '');
            } else {
                $row['signal'] = (string) ($hb['signal'] ?? 'unknown');
                if (!in_array($row['signal'], ['present', 'no_signal', 'unknown'], true)) {
                    $row['signal'] = 'unknown';
                }
                $row['sdi_lock'] = isset($hb['sdi_lock']) ? $hb['sdi_lock'] : null;
                if (!empty($hb['format'])) {
                    $row['format'] = (string) $hb['format'];
                }
                $row['detail'] = (string) ($probe['detail'] ?? 'unknown — needs DeckLink tools');
                if (!empty($hb['format'])) {
                    $row['detail'] .= ' Last known format ' . $hb['format'] . '.';
                }
            }
        } else {
            $row['transport'] = in_array($type, ['rtsp', 'srt', 'udp', 'tcp', 'rtp'], true) ? 'ip' : ($type === 'testsrc' ? 'demo' : 'other');
            $stored = (string) ($hb['signal'] ?? '');
            if (in_array($stored, ['receiving', 'stalled', 'down'], true) && $seen !== '') {
                $row['signal'] = nexrec_ops_classify_ip($seen, $lastChunk, $segment);
            } elseif ($seen !== '' || $lastChunk) {
                $row['signal'] = nexrec_ops_classify_ip($seen !== '' ? $seen : null, $lastChunk, $segment);
            } else {
                $row['signal'] = ((int) $inp['enabled'] === 1) ? 'down' : 'unknown';
                $row['detail'] = 'no record-worker heartbeat yet';
            }
        }
        $out[] = $row;
    }
    return $out;
}

if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
    return;
}

require_once __DIR__ . '/nexrec-auth-lib.php';

header('Content-Type: application/json');
header('Cache-Control: no-store');

$raw = file_get_contents('php://input');
$body = [];
if (is_string($raw) && trim($raw) !== '') {
    $j = json_decode($raw, true);
    if (is_array($j)) {
        $body = $j;
    }
}
$action = $_GET['action'] ?? ($body['action'] ?? 'units');
$action = is_string($action) ? trim($action) : 'units';

try {
    if ($action === 'control') {
        nexrec_require_roles(['admin']);
    } else {
        nexrec_require_roles(['admin', 'operator']);
    }
    nexrec_load_station_env();
    nexrec_migrate();

    if ($action === 'units') {
        $me = nexrec_me_payload();
        echo json_encode([
            'ok' => true,
            'units' => nexrec_ops_units_payload(),
            'can_control' => (($me['role'] ?? '') === 'admin'),
            'helper' => nexrec_ops_wrapper_path(),
        ], JSON_UNESCAPED_SLASHES);
        exit;
    }
    if ($action === 'journal') {
        $unit = (string) ($_GET['unit'] ?? $body['unit'] ?? '');
        $n = (int) ($_GET['n'] ?? $body['n'] ?? 80);
        if (!nexrec_ops_unit_allowed($unit)) {
            http_response_code(400);
            echo json_encode(['ok' => false, 'error' => 'unit not allowed']);
            exit;
        }
        $jrn = nexrec_ops_journal($unit, $n);
        echo json_encode(['ok' => true] + $jrn, JSON_UNESCAPED_SLASHES);
        exit;
    }
    if ($action === 'control') {
        $verb = (string) ($body['verb'] ?? $_GET['verb'] ?? '');
        $unit = (string) ($body['unit'] ?? $_GET['unit'] ?? '');
        $result = nexrec_ops_control($verb, $unit);
        if (!$result['ok']) {
            http_response_code(502);
        }
        echo json_encode($result, JSON_UNESCAPED_SLASHES);
        exit;
    }
    if ($action === 'signals') {
        echo json_encode(['ok' => true, 'signals' => nexrec_ops_signals()], JSON_UNESCAPED_SLASHES);
        exit;
    }
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => 'unknown action']);
} catch (InvalidArgumentException $e) {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()]);
} catch (RuntimeException $e) {
    $msg = $e->getMessage();
    http_response_code($msg === 'forbidden' ? 403 : ($msg === 'unauthorized' ? 401 : 500));
    echo json_encode(['ok' => false, 'error' => $msg]);
}

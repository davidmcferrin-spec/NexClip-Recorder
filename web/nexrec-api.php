<?php
/**
 * Recorder JSON API: inputs, chunks, exports, settings, nexclip, demo seed.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';
require_once __DIR__ . '/nexrec-decklink.php';
require_once __DIR__ . '/nexrec-forecast.php';
require_once __DIR__ . '/nexrec-asrun.php';
require_once __DIR__ . '/nexrec-deliver.php';

function nexrec_api_fail(int $status, string $message): never {
    if (!headers_sent()) {
        header('Content-Type: application/json');
        header('Cache-Control: no-store');
    }
    http_response_code($status);
    echo json_encode(['ok' => false, 'error' => $message], JSON_UNESCAPED_SLASHES);
    exit;
}

function nexrec_api_ok(array $extra = []): never {
    if (!headers_sent()) {
        header('Content-Type: application/json');
        header('Cache-Control: no-store');
    }
    echo json_encode(array_merge(['ok' => true], $extra), JSON_UNESCAPED_SLASHES);
    exit;
}

function nexrec_api_body(): array {
    $raw = file_get_contents('php://input');
    if (!is_string($raw) || trim($raw) === '') {
        return [];
    }
    $j = json_decode($raw, true);
    return is_array($j) ? $j : [];
}

function nexrec_forecast_span(?string $first, ?string $last, float $duration): float {
    $a = is_string($first) && $first !== '' ? strtotime($first) : false;
    $b = is_string($last) && $last !== '' ? strtotime($last) : false;
    $span = ($a !== false && $b !== false && $b > $a) ? (float) ($b - $a) : 0.0;
    if ($duration > $span) {
        $span = $duration;
    }
    return $span;
}

function nexrec_storage_forecast_now(): array {
    $storage = nexrec_storage_dir();
    $total = 0;
    $free = 0;
    if ($storage !== '' && is_dir($storage)) {
        $t = @disk_total_space($storage);
        $f = @disk_free_space($storage);
        if (is_int($t) || is_float($t)) {
            $total = (int) $t;
        }
        if (is_int($f) || is_float($f)) {
            $free = (int) $f;
        }
    }
    $usage = [];
    $res = nexrec_db()->query(
        'SELECT input_id, COALESCE(SUM(size_bytes), 0) AS stored, MIN(start_at) AS first_at,
                MAX(COALESCE(end_at, start_at)) AS last_at, COALESCE(SUM(duration_s), 0) AS duration_s
         FROM chunks WHERE ready = 1 AND orphan = 0 GROUP BY input_id'
    );
    if ($res !== false) {
        while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
            $usage[(string) $row['input_id']] = $row;
        }
    }
    $inputs = [];
    $res2 = nexrec_db()->query(
        'SELECT id, name, enabled, retention_days, video_bitrate, audio_bitrate FROM inputs ORDER BY name'
    );
    if ($res2 !== false) {
        while ($row = $res2->fetchArray(SQLITE3_ASSOC)) {
            $id = (string) $row['id'];
            $u = $usage[$id] ?? null;
            $inputs[] = [
                'id' => $id,
                'name' => (string) ($row['name'] ?? $id),
                'path' => nexrec_input_record_dir($id),
                'enabled' => (int) ($row['enabled'] ?? 0) === 1,
                'retention_days' => (int) ($row['retention_days'] ?? 0),
                'video_bitrate' => $row['video_bitrate'] ?? '',
                'audio_bitrate' => $row['audio_bitrate'] ?? '',
                'stored_bytes' => $u ? (int) $u['stored'] : 0,
                'span_seconds' => $u ? nexrec_forecast_span(
                    isset($u['first_at']) ? (string) $u['first_at'] : null,
                    isset($u['last_at']) ? (string) $u['last_at'] : null,
                    (float) ($u['duration_s'] ?? 0)
                ) : 0.0,
            ];
        }
    }
    return nexrec_storage_forecast(
        ['free_bytes' => $free, 'total_bytes' => $total],
        $inputs,
        [
            'max_used_percent' => nexrec_setting_int('storage.max_used_percent', 90),
            'warn_points' => nexrec_setting_int('storage.purge_warn_points', 5),
            'floor_bytes' => nexrec_parse_size_bytes(nexrec_setting('storage.free_space_floor')),
            'default_video' => nexrec_setting('ffmpeg.video_bitrate') ?: '12M',
            'default_audio' => nexrec_setting('ffmpeg.audio_bitrate') ?: '192k',
        ]
    );
}

function nexrec_input_record_dir(string $id): string {
    if (!nexrec_valid_input_id($id)) {
        return '';
    }
    return rtrim(nexrec_storage_dir(), '/\\') . '/inputs/' . $id . '/native';
}

function nexrec_storage_dir(): string {
    nexrec_load_station_env();
    if (function_exists('nexrec_setting')) {
        $p = nexrec_setting('storage.recordings');
        if ($p !== '') {
            return rtrim($p, '/');
        }
    }
    $p = getenv('NEXREC_STORAGE_DIR');
    if (is_string($p) && $p !== '') {
        return rtrim($p, '/');
    }
    return nexrec_data_dir() . '/storage';
}

function nexrec_exports_dir(): string {
    if (function_exists('nexrec_setting')) {
        $p = nexrec_setting('storage.exports');
        if ($p !== '') {
            return rtrim($p, '/\\');
        }
    }
    return nexrec_storage_dir() . '/exports';
}

function nexrec_export_file_allowed(string $path): bool {
    $realFile = realpath($path);
    $realRoot = realpath(nexrec_exports_dir());
    if ($realFile === false || $realRoot === false) {
        return false;
    }
    $prefix = rtrim($realRoot, '/\\') . DIRECTORY_SEPARATOR;
    return $realFile === $realRoot || str_starts_with($realFile, $prefix);
}

function nexrec_release_session(): void {
    if (session_status() === PHP_SESSION_ACTIVE) {
        session_write_close();
    }
}

/**
 * One bytes range. null means 416. An empty header is the whole file (not partial).
 * A satisfiable Range header is partial even when it covers every byte.
 *
 * @return array{0:int,1:int,2:bool}|null
 */
function nexrec_parse_byte_range(string $header, int $size): ?array {
    $header = trim($header);
    if ($header === '') {
        return [0, $size > 0 ? $size - 1 : -1, false];
    }
    if (preg_match('/^bytes=\s*(\d*)-(\d*)$/i', $header, $m) !== 1 || str_contains($header, ',')) {
        return [0, $size > 0 ? $size - 1 : -1, false];
    }
    $startRaw = $m[1];
    $endRaw = $m[2];
    if ($startRaw === '' && $endRaw === '') {
        return [0, $size > 0 ? $size - 1 : -1, false];
    }
    if ($size <= 0) {
        return null;
    }
    if ($startRaw === '') {
        $suffix = (int) $endRaw;
        if ($suffix <= 0) {
            return null;
        }
        $start = max(0, $size - $suffix);
        return [$start, $size - 1, true];
    }
    $start = (int) $startRaw;
    if ($start >= $size) {
        return null;
    }
    $end = $endRaw === '' ? $size - 1 : min((int) $endRaw, $size - 1);
    if ($end < $start) {
        return null;
    }
    return [$start, $end, true];
}

/**
 * @return array{status:int,start:int,length:int,headers:array<string,string>}
 */
function nexrec_media_plan(string $rangeHeader, int $size, string $contentType, string $downloadName = '', string $cacheControl = 'private, max-age=60'): array {
    $headers = [
        'Content-Type' => $contentType,
        'Accept-Ranges' => 'bytes',
        'Cache-Control' => $cacheControl,
    ];
    if ($downloadName !== '') {
        $safe = str_replace(["\r", "\n", '"'], '', $downloadName);
        $headers['Content-Disposition'] = 'attachment; filename="' . $safe . '"';
    }
    $range = nexrec_parse_byte_range($rangeHeader, $size);
    if ($range === null) {
        $headers['Content-Range'] = 'bytes */' . $size;
        return ['status' => 416, 'start' => 0, 'length' => 0, 'headers' => $headers];
    }
    $start = $range[0];
    $end = $range[1];
    $length = $end >= $start ? ($end - $start + 1) : 0;
    if ($range[2]) {
        $headers['Content-Range'] = 'bytes ' . $start . '-' . $end . '/' . $size;
        $status = 206;
    } else {
        $status = 200;
    }
    $headers['Content-Length'] = (string) $length;
    return ['status' => $status, 'start' => $start, 'length' => $length, 'headers' => $headers];
}

function nexrec_send_media_file(string $path, string $contentType, string $downloadName = '', string $cacheControl = 'private, max-age=60'): never {
    nexrec_release_session();
    $size = is_file($path) ? filesize($path) : false;
    if ($size === false || !is_readable($path)) {
        nexrec_api_fail(404, 'not found');
    }
    $rangeHeader = $_SERVER['HTTP_RANGE'] ?? '';
    if (!is_string($rangeHeader)) {
        $rangeHeader = '';
    }
    $plan = nexrec_media_plan($rangeHeader, $size, $contentType, $downloadName, $cacheControl);
    ini_set('zlib.output_compression', '0');
    while (ob_get_level() > 0) {
        ob_end_clean();
    }
    if (function_exists('apache_setenv')) {
        apache_setenv('no-gzip', '1');
    }
    set_time_limit(0);
    http_response_code($plan['status']);
    foreach ($plan['headers'] as $name => $value) {
        header($name . ': ' . $value);
    }
    if ($plan['status'] === 416 || $plan['length'] === 0) {
        exit;
    }
    $fh = fopen($path, 'rb');
    if ($fh === false) {
        exit;
    }
    if ($plan['start'] > 0 && fseek($fh, $plan['start']) !== 0) {
        fclose($fh);
        exit;
    }
    $left = $plan['length'];
    while ($left > 0 && !feof($fh) && connection_status() === CONNECTION_NORMAL) {
        $buf = fread($fh, (int) min(262144, $left));
        if (!is_string($buf) || $buf === '') {
            break;
        }
        echo $buf;
        $left -= strlen($buf);
        flush();
    }
    fclose($fh);
    exit;
}

function nexrec_valid_input_id(string $id): bool {
    return (bool) preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $id);
}

function nexrec_flag(array $body, string $key, int $default = 0): int {
    if (!array_key_exists($key, $body)) {
        return $default;
    }
    $v = $body[$key];
    if ($v === true || $v === 1 || $v === '1' || $v === 'true' || $v === 'on') {
        return 1;
    }
    return 0;
}

function nexrec_float_body(array $body, string $key, float $default): float {
    if (!array_key_exists($key, $body) || $body[$key] === '' || $body[$key] === null) {
        return $default;
    }
    return (float) $body[$key];
}

function nexrec_share_plain(string $value, int $max): string {
    $value = str_replace(["\r\n", "\r"], "\n", $value);
    $value = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', '', $value) ?? '';
    if (function_exists('mb_substr')) {
        return mb_substr($value, 0, $max);
    }
    return substr($value, 0, $max);
}

function nexrec_share_title(string $value): string {
    $value = preg_replace('/\s+/', ' ', nexrec_share_plain($value, 200)) ?? '';
    return trim($value);
}

function nexrec_share_description(string $value): string {
    return trim(nexrec_share_plain($value, 2000));
}

/** @return list<string> */
function nexrec_export_input_ids(array $row): array {
    $raw = $row['input_ids'] ?? '[]';
    if (is_string($raw)) {
        $decoded = json_decode($raw, true);
    } else {
        $decoded = $raw;
    }
    if (!is_array($decoded)) {
        return [];
    }
    $out = [];
    foreach ($decoded as $id) {
        if (is_string($id) && $id !== '') {
            $out[] = $id;
        }
    }
    return $out;
}

/**
 * One MP4 per input. Several inputs use {id}_{input}.mp4 beside the stored path.
 *
 * @return list<array{input_id:string,path:string}>
 */
function nexrec_export_output_files(array $row): array {
    $id = (string) ($row['id'] ?? '');
    if (preg_match('/^[A-Za-z0-9_-]+$/', $id) !== 1) {
        return [];
    }
    $ids = nexrec_export_input_ids($row);
    $dir = nexrec_exports_dir();
    $stored = (string) ($row['path'] ?? '');
    if ($stored !== '') {
        $parent = dirname($stored);
        if ($parent !== '' && $parent !== '.' ) {
            $dir = $parent;
        }
    }
    if (count($ids) <= 1) {
        $path = $stored !== '' ? $stored : ($dir . DIRECTORY_SEPARATOR . $id . '.mp4');
        return [['input_id' => $ids[0] ?? '', 'path' => $path]];
    }
    $out = [];
    foreach ($ids as $iid) {
        if (preg_match('/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/', $iid) !== 1) {
            continue;
        }
        $out[] = [
            'input_id' => $iid,
            'path' => $dir . DIRECTORY_SEPARATOR . $id . '_' . $iid . '.mp4',
        ];
    }
    return $out;
}

function nexrec_export_by_share_token(string $token): ?array {
    if (preg_match('/^[a-f0-9]{32}$/', $token) !== 1) {
        return null;
    }
    $st = nexrec_db()->prepare('SELECT * FROM exports WHERE share_token = :t');
    $st->bindValue(':t', $token, SQLITE3_TEXT);
    return nexrec_row($st->execute());
}

function nexrec_share_open(array $row): bool {
    if ((int) ($row['auth_required'] ?? 0) !== 1) {
        return true;
    }
    return nexrec_me_payload() !== null;
}

/** @return array<string, string> */
function nexrec_input_name_map(): array {
    $names = [];
    $res = nexrec_db()->query('SELECT id, name FROM inputs');
    if ($res === false) {
        return $names;
    }
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $id = (string) $row['id'];
        $names[$id] = (string) ($row['name'] ?? $id);
    }
    return $names;
}

function nexrec_share_download_name(array $row, string $inputId): string {
    $title = trim((string) ($row['title'] ?? ''));
    $base = $title !== '' ? $title : (string) ($row['id'] ?? 'export');
    $base = preg_replace('/[^A-Za-z0-9._ -]+/', '', $base) ?? '';
    $base = trim($base);
    if ($base === '') {
        $base = 'export';
    }
    $suffix = $inputId !== '' ? ('-' . $inputId) : '';
    return $base . $suffix . '.mp4';
}

/** @return array<string, mixed> */
function nexrec_share_payload(array $row): array {
    $names = nexrec_input_name_map();
    $token = (string) ($row['share_token'] ?? '');
    $done = (string) ($row['status'] ?? '') === 'done';
    $files = [];
    foreach (nexrec_export_output_files($row) as $file) {
        $iid = $file['input_id'];
        $path = $file['path'];
        $ready = $done && $path !== '' && is_file($path) && nexrec_export_file_allowed($path);
        $item = [
            'input_id' => $iid,
            'name' => $names[$iid] ?? ($iid !== '' ? $iid : 'Export'),
            'ready' => $ready,
        ];
        if ($ready && $token !== '' && $iid !== '') {
            $item['href'] = '/api/share/' . $token . '/' . rawurlencode($iid);
        }
        $files[] = $item;
    }
    return [
        'title' => (string) ($row['title'] ?? ''),
        'description' => (string) ($row['description'] ?? ''),
        'status' => (string) ($row['status'] ?? ''),
        'quality' => (string) ($row['quality'] ?? ''),
        't_in' => (string) ($row['t_in'] ?? ''),
        't_out' => (string) ($row['t_out'] ?? ''),
        'auth_required' => (int) ($row['auth_required'] ?? 0) === 1,
        'files' => $files,
    ];
}

if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
    return;
}

nexrec_load_station_env();
nexrec_migrate();
nexrec_seed_admin();

$body = nexrec_api_body();
$action = $_GET['action'] ?? ($body['action'] ?? '');
$action = is_string($action) ? trim($action) : '';

try {
    if ($action === 'status') {
        nexrec_require_roles([]);
        $db = nexrec_db();
        $inputs = (int) $db->querySingle('SELECT COUNT(*) FROM inputs');
        $chunks = (int) $db->querySingle('SELECT COUNT(*) FROM chunks WHERE ready=1');
        $exports = (int) $db->querySingle("SELECT COUNT(*) FROM exports WHERE status='done'");
        $floor = nexrec_setting('storage.free_space_floor');
        $storage = nexrec_storage_dir();
        $free = is_dir($storage) ? (int) disk_free_space($storage) : 0;
        nexrec_api_ok([
            'instance_id' => nexrec_setting('station.instance_id') ?: null,
            'instance_name' => nexrec_setting('station.display_name') ?: null,
            'mode' => nexrec_setting('nexapp.mode') ?: 'standalone',
            'inputs' => $inputs,
            'chunks' => $chunks,
            'exports' => $exports,
            'storage_dir' => $storage,
            'free_bytes' => $free,
            'free_space_floor' => $floor,
            'segment_seconds' => nexrec_setting_int('ffmpeg.segment_seconds', 300),
            'max_inputs' => nexrec_setting_int('defaults.max_inputs', 10),
        ]);
    }

    if ($action === 'storage_forecast') {
        nexrec_require_roles([]);
        nexrec_api_ok(nexrec_storage_forecast_now());
    }

    if ($action === 'inputs_list') {
        nexrec_require_roles([]);
        $out = [];
        $res = nexrec_db()->query('SELECT * FROM inputs ORDER BY name');
        if ($res !== false) {
            while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
                $out[] = $row;
            }
        }
        nexrec_api_ok(['inputs' => $out]);
    }

    if ($action === 'decklink_devices') {
        nexrec_require_roles(['admin', 'operator']);
        nexrec_api_ok(nexrec_decklink_query_devices());
    }

    if ($action === 'decklink_formats') {
        nexrec_require_roles(['admin', 'operator']);
        $dev = trim((string) ($_GET['device'] ?? $body['device'] ?? ''));
        if (!nexrec_decklink_device_ok($dev)) {
            nexrec_api_fail(400, 'invalid decklink device');
        }
        nexrec_api_ok(nexrec_decklink_query_formats($dev));
    }

    if ($action === 'input_put') {
        nexrec_require_roles(['admin', 'operator']);
        $id = strtolower(trim((string) ($body['id'] ?? '')));
        if (!nexrec_valid_input_id($id)) {
            nexrec_api_fail(400, 'invalid id');
        }
        $max = nexrec_setting_int('defaults.max_inputs', 10);
        $n = (int) nexrec_db()->querySingle('SELECT COUNT(*) FROM inputs');
        $stExists = nexrec_db()->prepare('SELECT COUNT(*) FROM inputs WHERE id=:id');
        $stExists->bindValue(':id', $id, SQLITE3_TEXT);
        $exists = $stExists->execute()->fetchArray();
        $exists = $exists === false ? 0 : (int) array_values($exists)[0];
        $isNew = !(int) $exists;
        if (!$exists && $n >= $max) {
            nexrec_api_fail(400, "max {$max} inputs");
        }
        $type = strtolower((string) ($body['source_type'] ?? 'rtsp'));
        $allowed = ['rtsp', 'srt', 'udp', 'tcp', 'rtp', 'decklink', 'testsrc'];
        if (!in_array($type, $allowed, true)) {
            nexrec_api_fail(400, 'invalid source_type');
        }
        $deviceName = trim((string) ($body['decklink_device'] ?? ''));
        $formatCode = trim((string) ($body['decklink_format'] ?? ''));
        $deckErr = nexrec_decklink_input_error($type, $deviceName, $formatCode);
        if ($deckErr !== '') {
            nexrec_api_fail(400, $deckErr);
        }
        $now = nexrec_now_iso();
        $st = nexrec_db()->prepare(
            'INSERT INTO inputs (
               id,name,source_type,url,decklink_device,decklink_format,enabled,live_transcode,copy_native,upconvert_1080i,keep_interlace,
               video_bitrate,audio_bitrate,retention_days,preview_path,preview_enabled,
               feat_scte,feat_av_anomaly,feat_captions,feat_transcribe,feat_nielsen,feat_monitors,
               thresh_freeze_s,thresh_black_s,thresh_bars_s,transcribe_engine,nexclip_slot,
               asrun_offset_s,asrun_offset_frames,
               created_at,updated_at)
             VALUES (
               :id,:name,:t,:url,:dd,:df,:en,:lt,:cn,:up,:ki,:vb,:ab,:rd,:pp,:pe,
               :scte,:ava,:cc,:tr,:ni,:mon,:tf,:tb,:tbar,:teng,:slot,:aos,:aof,:c,:u)
             ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, source_type=excluded.source_type, url=excluded.url,
               decklink_device=excluded.decklink_device, decklink_format=excluded.decklink_format,
               enabled=excluded.enabled, live_transcode=excluded.live_transcode,
               copy_native=excluded.copy_native, upconvert_1080i=excluded.upconvert_1080i,
               keep_interlace=excluded.keep_interlace,
               video_bitrate=excluded.video_bitrate, audio_bitrate=excluded.audio_bitrate,
               retention_days=excluded.retention_days, preview_path=excluded.preview_path,
               preview_enabled=excluded.preview_enabled,
               feat_scte=excluded.feat_scte, feat_av_anomaly=excluded.feat_av_anomaly,
               feat_captions=excluded.feat_captions, feat_transcribe=excluded.feat_transcribe,
               feat_nielsen=excluded.feat_nielsen, feat_monitors=excluded.feat_monitors,
               thresh_freeze_s=excluded.thresh_freeze_s, thresh_black_s=excluded.thresh_black_s,
               thresh_bars_s=excluded.thresh_bars_s, transcribe_engine=excluded.transcribe_engine,
               nexclip_slot=excluded.nexclip_slot,
               asrun_offset_s=excluded.asrun_offset_s, asrun_offset_frames=excluded.asrun_offset_frames,
               updated_at=excluded.updated_at'
        );
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $st->bindValue(':name', (string) ($body['name'] ?? $id), SQLITE3_TEXT);
        $st->bindValue(':t', $type, SQLITE3_TEXT);
        $st->bindValue(':url', $body['url'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':dd', $deviceName, SQLITE3_TEXT);
        $st->bindValue(':df', $formatCode, SQLITE3_TEXT);
        $pick = static function (string $key, string $setting, int $omit) use ($body, $isNew): int {
            if (array_key_exists($key, $body)) {
                return !empty($body[$key]) ? 1 : 0;
            }
            if ($isNew) {
                return nexrec_setting_bool($setting) ? 1 : 0;
            }
            return $omit;
        };
        $st->bindValue(':en', !empty($body['enabled']) ? 1 : 0, SQLITE3_INTEGER);
        $lt = $pick('live_transcode', 'defaults.live_transcode', 0);
        $cn = $pick('copy_native', 'defaults.copy_native', 1);
        if ($type === 'decklink') {
            // Uncompressed SDI. The record argv always encodes; store that fact.
            $lt = 1;
            $cn = 0;
        }
        $st->bindValue(':lt', $lt, SQLITE3_INTEGER);
        $st->bindValue(':cn', $cn, SQLITE3_INTEGER);
        $st->bindValue(':up', $pick('upconvert_1080i', 'defaults.upconvert_1080i', 0), SQLITE3_INTEGER);
        if (array_key_exists('keep_interlace', $body)) {
            $ki = nexrec_flag($body, 'keep_interlace');
        } elseif ($type === 'decklink') {
            $ki = empty($body['upconvert_1080i']) ? 1 : 0;
        } else {
            $ki = 0;
        }
        $st->bindValue(':ki', $ki, SQLITE3_INTEGER);
        $st->bindValue(':vb', $body['video_bitrate'] ?? null, SQLITE3_TEXT);
        $st->bindValue(':ab', $body['audio_bitrate'] ?? null, SQLITE3_TEXT);
        if (array_key_exists('retention_days', $body) && $body['retention_days'] !== '' && $body['retention_days'] !== null) {
            $rd = (int) $body['retention_days'];
        } elseif ($isNew) {
            $rd = nexrec_setting_int('retention.raw_days', 28);
        } else {
            $rd = 28;
        }
        $st->bindValue(':rd', $rd, SQLITE3_INTEGER);
        $st->bindValue(':pp', $body['preview_path'] ?? ('in' . min($n, 9)), SQLITE3_TEXT);
        $st->bindValue(':pe', $pick('preview_enabled', 'defaults.preview_enabled', 1), SQLITE3_INTEGER);
        $feat = static function (string $key, string $setting) use ($body, $isNew): int {
            if (array_key_exists($key, $body)) {
                return nexrec_flag($body, $key);
            }
            return $isNew && nexrec_setting_bool($setting) ? 1 : 0;
        };
        $st->bindValue(':scte', $feat('feat_scte', 'defaults.feat_scte'), SQLITE3_INTEGER);
        $st->bindValue(':ava', $feat('feat_av_anomaly', 'defaults.feat_av_anomaly'), SQLITE3_INTEGER);
        $st->bindValue(':cc', $feat('feat_captions', 'defaults.feat_captions'), SQLITE3_INTEGER);
        $st->bindValue(':tr', $feat('feat_transcribe', 'defaults.feat_transcribe'), SQLITE3_INTEGER);
        $st->bindValue(':ni', $feat('feat_nielsen', 'defaults.feat_nielsen'), SQLITE3_INTEGER);
        $st->bindValue(':mon', $feat('feat_monitors', 'defaults.feat_monitors'), SQLITE3_INTEGER);
        $st->bindValue(':tf', nexrec_float_body($body, 'thresh_freeze_s', 2.0));
        $st->bindValue(':tb', nexrec_float_body($body, 'thresh_black_s', 2.0));
        $st->bindValue(':tbar', nexrec_float_body($body, 'thresh_bars_s', 5.0));
        if (array_key_exists('transcribe_engine', $body)) {
            $teng = (string) $body['transcribe_engine'];
        } elseif ($isNew) {
            $teng = nexrec_setting('intelligence.transcribe_engine');
            if ($teng === 'none') {
                $teng = '';
            }
        } else {
            $teng = '';
        }
        $st->bindValue(':teng', $teng, SQLITE3_TEXT);
        $slot = (int) ($body['nexclip_slot'] ?? 0);
        if ($slot < 1 || $slot > 8) {
            $slot = 0;
        }
        if ($slot === 0) {
            $st->bindValue(':slot', null, SQLITE3_NULL);
        } else {
            $st->bindValue(':slot', $slot, SQLITE3_INTEGER);
        }
        $offS = max(-86400, min(86400, (int) ($body['asrun_offset_s'] ?? 0)));
        $offF = max(-29, min(29, (int) ($body['asrun_offset_frames'] ?? 0)));
        $st->bindValue(':aos', $offS, SQLITE3_INTEGER);
        $st->bindValue(':aof', $offF, SQLITE3_INTEGER);
        $st->bindValue(':c', $now, SQLITE3_TEXT);
        $st->bindValue(':u', $now, SQLITE3_TEXT);
        $st->execute();
        nexrec_api_ok(['id' => $id]);
    }

    if ($action === 'input_delete') {
        nexrec_require_roles(['admin']);
        $id = (string) ($body['id'] ?? $_GET['id'] ?? '');
        if (!nexrec_valid_input_id($id)) {
            nexrec_api_fail(400, 'invalid id');
        }
        $db = nexrec_db();
        foreach (['events', 'captions', 'loudness_samples', 'analyze_jobs', 'chunks', 'input_heartbeats', 'asrun_inputs'] as $tbl) {
            $st = $db->prepare("DELETE FROM {$tbl} WHERE input_id=:i");
            $st->bindValue(':i', $id, SQLITE3_TEXT);
            $st->execute();
        }
        $stFts = $db->prepare('DELETE FROM captions_fts WHERE input_id=:i');
        $stFts->bindValue(':i', $id, SQLITE3_TEXT);
        $stFts->execute();
        $st = $db->prepare('DELETE FROM inputs WHERE id=:i');
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $st->execute();
        nexrec_api_ok(['deleted' => $id]);
    }

    if ($action === 'chunks_list') {
        nexrec_require_roles([]);
        $iid = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        $from = (string) ($_GET['t_from'] ?? $body['t_from'] ?? '');
        $to = (string) ($_GET['t_to'] ?? $body['t_to'] ?? '');
        $sql = 'SELECT * FROM chunks WHERE ready=1 AND orphan=0';
        if ($iid !== '') {
            $sql .= ' AND input_id = :i';
        }
        $window = $from !== '' || $to !== '';
        if ($window) {
            if ($from === '' || $to === '') {
                nexrec_api_fail(400, 't_from and t_to are both required');
            }
            $sql .= ' AND start_at < :t_to AND COALESCE(end_at, start_at) > :t_from';
        }
        $sql .= ' ORDER BY start_at DESC LIMIT ' . ($window ? '2000' : '500');
        $st = nexrec_db()->prepare($sql);
        if ($iid !== '') {
            $st->bindValue(':i', $iid, SQLITE3_TEXT);
        }
        if ($window) {
            $st->bindValue(':t_from', $from, SQLITE3_TEXT);
            $st->bindValue(':t_to', $to, SQLITE3_TEXT);
        }
        $res = $st->execute();
        $out = [];
        while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
            $out[] = $row;
        }
        nexrec_api_ok(['chunks' => $out]);
    }

    if ($action === 'exports_list') {
        nexrec_require_roles([]);
        $out = [];
        $res = nexrec_db()->query('SELECT * FROM exports ORDER BY created_at DESC LIMIT 100');
        if ($res !== false) {
            while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
                $out[] = $row;
            }
        }
        nexrec_api_ok(['exports' => $out]);
    }

    if ($action === 'export_create') {
        $me = nexrec_require_roles(['admin', 'operator']);
        $ids = $body['input_ids'] ?? [];
        if (!is_array($ids) || $ids === []) {
            nexrec_api_fail(400, 'input_ids required');
        }
        $tIn = (string) ($body['t_in'] ?? '');
        $tOut = (string) ($body['t_out'] ?? '');
        if ($tIn === '' || $tOut === '') {
            nexrec_api_fail(400, 't_in and t_out required');
        }
        $quality = (string) ($body['quality'] ?? 'full');
        if (!in_array($quality, ['full', 'proxy'], true)) {
            nexrec_api_fail(400, 'quality must be full or proxy');
        }
        $scope = (string) ($body['scope'] ?? 'one');
        if (!in_array($scope, ['one', 'all', 'pick'], true)) {
            $scope = 'one';
        }
        $days = nexrec_setting_int('retention.export_days', 15);
        $expId = nexrec_new_id('exp');
        $token = bin2hex(random_bytes(16));
        $title = nexrec_share_title((string) ($body['title'] ?? ''));
        $description = nexrec_share_description((string) ($body['description'] ?? ''));
        $authRequired = nexrec_flag($body, 'auth_required', 0);
        $protected = !empty($body['protected']) ? 1 : 0;
        $destinationIds = $body['destination_ids'] ?? [];
        if (!is_array($destinationIds)) {
            nexrec_api_fail(400, 'destination_ids must be a list');
        }
        $destinationIds = nexrec_deliver_ids($destinationIds);
        $expires = $protected ? null : gmdate('Y-m-d\TH:i:s\Z', time() + $days * 86400);
        $st = nexrec_db()->prepare(
            "INSERT INTO exports (id,status,input_ids,t_in,t_out,quality,scope,path,size_bytes,protected,error,created_by,created_at,expires_at,nexclip_schedule_id,title,description,auth_required,share_token)
             VALUES (:id,'queued',:ids,:tin,:tout,:q,:sc,NULL,NULL,:p,NULL,:by,:c,:e,NULL,:title,:descr,:auth,:token)"
        );
        $st->bindValue(':id', $expId, SQLITE3_TEXT);
        $st->bindValue(':ids', json_encode(array_values($ids)), SQLITE3_TEXT);
        $st->bindValue(':tin', $tIn, SQLITE3_TEXT);
        $st->bindValue(':tout', $tOut, SQLITE3_TEXT);
        $st->bindValue(':q', $quality, SQLITE3_TEXT);
        $st->bindValue(':sc', $scope, SQLITE3_TEXT);
        $st->bindValue(':p', $protected, SQLITE3_INTEGER);
        $st->bindValue(':by', $me['username'], SQLITE3_TEXT);
        $st->bindValue(':c', nexrec_now_iso(), SQLITE3_TEXT);
        $st->bindValue(':e', $expires, SQLITE3_TEXT);
        $st->bindValue(':title', $title, SQLITE3_TEXT);
        $st->bindValue(':descr', $description, SQLITE3_TEXT);
        $st->bindValue(':auth', $authRequired, SQLITE3_INTEGER);
        $st->bindValue(':token', $token, SQLITE3_TEXT);
        $st->execute();
        nexrec_deliver_attach($expId, $destinationIds);
        nexrec_api_ok(['export_id' => $expId, 'share_token' => $token]);
    }

    if ($action === 'export_share_update') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT id, share_token FROM exports WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $row = nexrec_row($st->execute());
        if ($row === null) {
            nexrec_api_fail(404, 'not found');
        }
        $title = nexrec_share_title((string) ($body['title'] ?? ''));
        $description = nexrec_share_description((string) ($body['description'] ?? ''));
        $authRequired = nexrec_flag($body, 'auth_required', 0);
        $token = (string) ($row['share_token'] ?? '');
        if (preg_match('/^[a-f0-9]{32}$/', $token) !== 1) {
            $token = bin2hex(random_bytes(16));
        }
        $up = nexrec_db()->prepare(
            'UPDATE exports SET title=:title, description=:descr, auth_required=:auth, share_token=:token WHERE id=:id'
        );
        $up->bindValue(':title', $title, SQLITE3_TEXT);
        $up->bindValue(':descr', $description, SQLITE3_TEXT);
        $up->bindValue(':auth', $authRequired, SQLITE3_INTEGER);
        $up->bindValue(':token', $token, SQLITE3_TEXT);
        $up->bindValue(':id', $id, SQLITE3_TEXT);
        $up->execute();
        nexrec_api_ok(['id' => $id, 'share_token' => $token]);
    }

    if ($action === 'share_get' || $action === 'share_file') {
        $token = (string) ($_GET['token'] ?? $body['token'] ?? '');
        $row = nexrec_export_by_share_token($token);
        if ($row === null) {
            nexrec_api_fail(404, 'not found');
        }
        if (!nexrec_share_open($row)) {
            nexrec_api_fail(401, 'unauthorized');
        }
        if ($action === 'share_get') {
            nexrec_api_ok(['share' => nexrec_share_payload($row)]);
        }
        $want = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        $match = null;
        foreach (nexrec_export_output_files($row) as $file) {
            if ($file['input_id'] === $want) {
                $match = $file;
                break;
            }
        }
        $path = (string) ($match['path'] ?? '');
        if ((string) ($row['status'] ?? '') !== 'done' || $match === null || !is_file($path) || !nexrec_export_file_allowed($path)) {
            nexrec_api_fail(404, 'not found');
        }
        nexrec_send_media_file($path, 'video/mp4', nexrec_share_download_name($row, $want), 'private, no-store');
    }

    if ($action === 'export_protect') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        $st = nexrec_db()->prepare('UPDATE exports SET protected=:p, expires_at=CASE WHEN :p=1 THEN NULL ELSE expires_at END WHERE id=:id');
        $st->bindValue(':p', !empty($body['protected']) ? 1 : 0, SQLITE3_INTEGER);
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $st->execute();
        nexrec_api_ok(['id' => $id]);
    }

    if ($action === 'export_cancel') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT status FROM exports WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        if (!$row) {
            nexrec_api_fail(404, 'not found');
        }
        $status = (string) $row['status'];
        if ($status === 'queued') {
            $up = nexrec_db()->prepare(
                "UPDATE exports SET status='cancelled', finished_at=:t, cancel_requested=0 WHERE id=:id AND status='queued'"
            );
            $up->bindValue(':t', nexrec_now_iso(), SQLITE3_TEXT);
            $up->bindValue(':id', $id, SQLITE3_TEXT);
            $up->execute();
            nexrec_deliver_cancel_waiting($id);
        } elseif ($status === 'running') {
            $up = nexrec_db()->prepare('UPDATE exports SET cancel_requested=1 WHERE id=:id AND status=\'running\'');
            $up->bindValue(':id', $id, SQLITE3_TEXT);
            $up->execute();
        } else {
            nexrec_api_fail(400, 'not in the queue');
        }
        nexrec_api_ok(['id' => $id, 'status' => $status === 'queued' ? 'cancelled' : 'running']);
    }

    if ($action === 'export_retry') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT status FROM exports WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $row = nexrec_row($st->execute());
        if ($row === null) {
            nexrec_api_fail(404, 'not found');
        }
        $status = (string) ($row['status'] ?? '');
        if (!in_array($status, ['error', 'cancelled', 'done'], true)) {
            nexrec_api_fail(400, 'not retryable');
        }
        $up = nexrec_db()->prepare(
            "UPDATE exports SET status='queued', error=NULL, progress_pct=NULL, progress_at=NULL,
             started_at=NULL, finished_at=NULL, encode_mode='', cancel_requested=0, path=NULL, size_bytes=NULL
             WHERE id=:id AND status IN ('error','cancelled','done')"
        );
        $up->bindValue(':id', $id, SQLITE3_TEXT);
        $up->execute();
        $stopping = nexrec_deliver_hold($id);
        nexrec_api_ok(['id' => $id, 'transfer_stopping' => $stopping]);
    }

    if ($action === 'export_remove') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT status, path FROM exports WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        if (!$row) {
            nexrec_api_fail(404, 'not found');
        }
        if ((string) $row['status'] === 'running') {
            nexrec_api_fail(400, 'cancel the running export first');
        }
        $busy = nexrec_db()->prepare(
            "SELECT id FROM deliveries WHERE export_id=:id AND status IN ('queued','running') LIMIT 1"
        );
        $busy->bindValue(':id', $id, SQLITE3_TEXT);
        if (nexrec_row($busy->execute()) !== null) {
            nexrec_api_fail(400, 'cancel the transfer first');
        }
        $drop = nexrec_db()->prepare('DELETE FROM deliveries WHERE export_id=:id');
        $drop->bindValue(':id', $id, SQLITE3_TEXT);
        $drop->execute();
        $path = (string) ($row['path'] ?? '');
        if ($path !== '' && is_file($path) && nexrec_export_file_allowed($path)) {
            unlink($path);
        }
        $del = nexrec_db()->prepare('DELETE FROM exports WHERE id=:id AND status<>\'running\'');
        $del->bindValue(':id', $id, SQLITE3_TEXT);
        $del->execute();
        nexrec_api_ok(['id' => $id]);
    }

    if ($action === 'chunk_media') {
        if (session_status() !== PHP_SESSION_ACTIVE) {
            session_cache_limiter('');
        }
        nexrec_require_roles([]);
        $id = (string) ($_GET['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT path FROM chunks WHERE id=:i AND ready=1');
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        if (!$row || !is_file($row['path'])) {
            nexrec_release_session();
            nexrec_api_fail(404, 'not found');
        }
        nexrec_send_media_file($row['path'], 'video/mp4');
    }

    if ($action === 'chunk_thumb') {
        if (session_status() !== PHP_SESSION_ACTIVE) {
            session_cache_limiter('');
        }
        nexrec_require_roles([]);
        $id = (string) ($_GET['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT path FROM chunks WHERE id=:i AND ready=1 AND orphan=0');
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        $src = (string) ($row['path'] ?? '');
        $thumb = $src !== '' ? $src . '.jpg' : '';
        $realSrc = $src !== '' ? realpath($src) : false;
        $realThumb = $thumb !== '' ? realpath($thumb) : false;
        if (!$row || $realSrc === false || $realThumb === false || $realThumb !== $realSrc . '.jpg' || !is_file($realThumb)) {
            nexrec_release_session();
            nexrec_api_fail(404, 'not found');
        }
        nexrec_send_media_file($realThumb, 'image/jpeg', '', 'private, max-age=86400');
    }

    if ($action === 'export_file') {
        if (session_status() !== PHP_SESSION_ACTIVE) {
            session_cache_limiter('');
        }
        nexrec_require_roles([]);
        $id = (string) ($_GET['id'] ?? '');
        $st = nexrec_db()->prepare("SELECT path FROM exports WHERE id=:i AND status='done'");
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        $path = (string) ($row['path'] ?? '');
        if (!$row || !is_file($path) || !nexrec_export_file_allowed($path)) {
            nexrec_release_session();
            nexrec_api_fail(404, 'not found');
        }
        nexrec_send_media_file($path, 'video/mp4', basename($path), 'private, no-store');
    }

    if ($action === 'settings_get') {
        $me = nexrec_require_roles(['admin', 'operator']);
        $pub = nexrec_settings_public();
        $pub['can_edit'] = (($me['role'] ?? '') === 'admin');
        nexrec_api_ok($pub);
    }

    if ($action === 'settings_patch') {
        $me = nexrec_require_roles(['admin']);
        $incoming = $body['settings'] ?? null;
        if (!is_array($incoming)) {
            nexrec_api_fail(400, 'settings object required');
        }
        try {
            $updated = nexrec_settings_put($incoming, (string) ($me['username'] ?? 'admin'));
        } catch (InvalidArgumentException $e) {
            nexrec_api_fail(400, $e->getMessage());
        }
        $pub = nexrec_settings_public();
        $pub['can_edit'] = true;
        $pub['updated'] = $updated;
        nexrec_api_ok($pub);
    }

    if ($action === 'demo_seed') {
        nexrec_require_roles(['admin', 'operator']);
        $now = time();
        $id = 'demo';
        $exists = nexrec_db()->querySingle("SELECT COUNT(*) FROM inputs WHERE id='demo'");
        if (!$exists) {
            $st = nexrec_db()->prepare(
                "INSERT INTO inputs (id,name,source_type,url,decklink_device,decklink_format,enabled,live_transcode,copy_native,upconvert_1080i,video_bitrate,audio_bitrate,retention_days,preview_path,preview_enabled,created_at,updated_at)
                 VALUES ('demo','Demo color bars','testsrc','', '', '', 1, 1, 0, 0, '4M', '128k', 28, 'in0', 1, :c, :u)"
            );
            $st->bindValue(':c', nexrec_now_iso(), SQLITE3_TEXT);
            $st->bindValue(':u', nexrec_now_iso(), SQLITE3_TEXT);
            $st->execute();
        }
        // Fixture chunks so the export editor can render without a live record.
        $n = (int) nexrec_db()->querySingle("SELECT COUNT(*) FROM chunks WHERE input_id='demo'");
        if ($n === 0) {
            for ($i = 0; $i < 6; $i++) {
                $start = gmdate('Y-m-d\TH:i:s\Z', $now - (6 - $i) * 300);
                $end = gmdate('Y-m-d\TH:i:s\Z', $now - (5 - $i) * 300);
                $cid = nexrec_new_id('chk');
                $st = nexrec_db()->prepare(
                    "INSERT INTO chunks (id,input_id,path,kind,start_at,end_at,duration_s,size_bytes,width,height,fps,interlaced,codec,timecode_start,ready,orphan,created_at)
                     VALUES (:id,'demo',:p,'native',:s,:e,300,0,1280,720,30,0,'h264',NULL,1,0,:c)"
                );
                $st->bindValue(':id', $cid, SQLITE3_TEXT);
                $st->bindValue(':p', '/tmp/nexrec-fixture-' . $i . '.mp4', SQLITE3_TEXT);
                $st->bindValue(':s', $start, SQLITE3_TEXT);
                $st->bindValue(':e', $end, SQLITE3_TEXT);
                $st->bindValue(':c', nexrec_now_iso(), SQLITE3_TEXT);
                $st->execute();
            }
        }
        nexrec_api_ok(['seeded' => true]);
    }

    if ($action === 'events_list') {
        nexrec_require_roles([]);
        $iid = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        $kind = (string) ($_GET['kind'] ?? $body['kind'] ?? '');
        $sql = 'SELECT * FROM events WHERE 1=1';
        if ($iid !== '') {
            $sql .= ' AND input_id = :i';
        }
        if ($kind !== '') {
            $sql .= ' AND kind = :k';
        }
        $sql .= ' ORDER BY t_start DESC LIMIT 200';
        $st = nexrec_db()->prepare($sql);
        if ($iid !== '') {
            $st->bindValue(':i', $iid, SQLITE3_TEXT);
        }
        if ($kind !== '') {
            $st->bindValue(':k', $kind, SQLITE3_TEXT);
        }
        $res = $st->execute();
        $out = [];
        while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
            $out[] = $row;
        }
        nexrec_api_ok(['events' => $out]);
    }

    if ($action === 'captions_window') {
        nexrec_require_roles([]);
        $iid = strtolower(trim((string) ($_GET['input_id'] ?? $body['input_id'] ?? '')));
        if (!nexrec_valid_input_id($iid)) {
            nexrec_api_fail(400, 'input_id required');
        }
        $fromRaw = (string) ($_GET['t_from'] ?? $body['t_from'] ?? '');
        $toRaw = (string) ($_GET['t_to'] ?? $body['t_to'] ?? '');
        $fromTs = strtotime($fromRaw);
        $toTs = strtotime($toRaw);
        if ($fromTs === false || $toTs === false || $toTs <= $fromTs) {
            nexrec_api_fail(400, 't_from and t_to required');
        }
        if (($toTs - $fromTs) > 6 * 3600) {
            nexrec_api_fail(400, 'window longer than 6 hours');
        }
        $fromIso = gmdate('Y-m-d\TH:i:s\Z', $fromTs);
        $toIso = gmdate('Y-m-d\TH:i:s\Z', $toTs);
        $st = nexrec_db()->prepare(
            'SELECT id, t_start, t_end, text, service, kind FROM captions
             WHERE input_id = :i AND kind = :k
               AND t_start < :to
               AND (t_end IS NULL OR t_end = \'\' OR t_end > :from)
             ORDER BY t_start ASC
             LIMIT 500'
        );
        $st->bindValue(':i', $iid, SQLITE3_TEXT);
        $st->bindValue(':k', 'caption', SQLITE3_TEXT);
        $st->bindValue(':from', $fromIso, SQLITE3_TEXT);
        $st->bindValue(':to', $toIso, SQLITE3_TEXT);
        $res = $st->execute();
        $cues = [];
        while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
            $text = trim((string) ($row['text'] ?? ''));
            if ($text === '') {
                continue;
            }
            $cues[] = [
                'id' => $row['id'] ?? '',
                't_start' => $row['t_start'] ?? '',
                't_end' => $row['t_end'] ?? '',
                'text' => $text,
                'service' => $row['service'] ?? '',
            ];
        }
        nexrec_api_ok(['cues' => $cues, 'input_id' => $iid]);
    }

    if ($action === 'search_text') {
        nexrec_require_roles([]);
        $q = trim((string) ($_GET['q'] ?? $body['q'] ?? ''));
        $iid = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        if ($q === '') {
            nexrec_api_ok(['hits' => [], 'engine' => 'none']);
        }
        $limit = 50;
        $hits = [];
        $engine = 'like';
        $ftsOk = true;
        try {
            $sql = "SELECT id, input_id, kind, t_start, speaker, text FROM captions_fts WHERE tsv @@ plainto_tsquery('simple', :q)";
            if ($iid !== '') {
                $sql .= ' AND input_id = :i';
            }
            $sql .= ' LIMIT CAST(:n AS integer)';
            $st = nexrec_db()->prepare($sql);
            $st->bindValue(':q', $q, SQLITE3_TEXT);
            if ($iid !== '') {
                $st->bindValue(':i', $iid, SQLITE3_TEXT);
            }
            $st->bindValue(':n', $limit, SQLITE3_INTEGER);
            $res = $st->execute();
            while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
                $hits[] = $row;
            }
            $engine = 'tsvector';
        } catch (Throwable $e) {
            $ftsOk = false;
        }
        if (!$ftsOk) {
            $sql = 'SELECT id, input_id, kind, service, speaker, t_start, t_end, text FROM captions WHERE text LIKE :q';
            if ($iid !== '') {
                $sql .= ' AND input_id = :i';
            }
            $sql .= ' ORDER BY t_start DESC LIMIT CAST(:n AS integer)';
            $st = nexrec_db()->prepare($sql);
            $st->bindValue(':q', '%' . $q . '%', SQLITE3_TEXT);
            if ($iid !== '') {
                $st->bindValue(':i', $iid, SQLITE3_TEXT);
            }
            $st->bindValue(':n', $limit, SQLITE3_INTEGER);
            $res = $st->execute();
            $hits = [];
            while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
                $hits[] = $row;
            }
            $engine = 'like';
        }
        nexrec_api_ok(['hits' => $hits, 'engine' => $engine, 'q' => $q]);
    }

    if ($action === 'loudness_chart') {
        nexrec_require_roles([]);
        $iid = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        $tIn = (string) ($_GET['t_in'] ?? $body['t_in'] ?? '');
        $tOut = (string) ($_GET['t_out'] ?? $body['t_out'] ?? '');
        $sql = 'SELECT * FROM loudness_samples WHERE 1=1';
        if ($iid !== '') {
            $sql .= ' AND input_id = :i';
        }
        if ($tIn !== '') {
            $sql .= ' AND t_at >= :tin';
        }
        if ($tOut !== '') {
            $sql .= ' AND t_at <= :tout';
        }
        $sql .= ' ORDER BY t_at ASC LIMIT 2000';
        $st = nexrec_db()->prepare($sql);
        if ($iid !== '') {
            $st->bindValue(':i', $iid, SQLITE3_TEXT);
        }
        if ($tIn !== '') {
            $st->bindValue(':tin', $tIn, SQLITE3_TEXT);
        }
        if ($tOut !== '') {
            $st->bindValue(':tout', $tOut, SQLITE3_TEXT);
        }
        $res = $st->execute();
        $out = [];
        while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
            $out[] = $row;
        }
        nexrec_api_ok([
            'samples' => $out,
            'target_lkfs' => -24.0,
            'standard' => 'ITU-R BS.1770 / ATSC A/85 (CALM)',
            'note' => 'Measure enqueues an ebur128 job; PHP does not shell FFmpeg.',
        ]);
    }

    if ($action === 'loudness_enqueue') {
        nexrec_require_roles(['admin', 'operator']);
        $iid = strtolower(trim((string) ($body['input_id'] ?? '')));
        if (!nexrec_valid_input_id($iid)) {
            nexrec_api_fail(400, 'input_id required');
        }
        $tIn = (string) ($body['t_in'] ?? '');
        $tOut = (string) ($body['t_out'] ?? '');
        if ($tIn === '' || $tOut === '') {
            nexrec_api_fail(400, 't_in and t_out required');
        }
        $jid = nexrec_new_id('anl');
        $now = nexrec_now_iso();
        $st = nexrec_db()->prepare(
            "INSERT INTO analyze_jobs (id,status,kind,input_id,export_id,t_in,t_out,path,error,result_json,created_at,updated_at)
             VALUES (:id,'queued','loudness',:i,NULL,:tin,:tout,NULL,NULL,NULL,:c,:u)"
        );
        $st->bindValue(':id', $jid, SQLITE3_TEXT);
        $st->bindValue(':i', $iid, SQLITE3_TEXT);
        $st->bindValue(':tin', $tIn, SQLITE3_TEXT);
        $st->bindValue(':tout', $tOut, SQLITE3_TEXT);
        $st->bindValue(':c', $now, SQLITE3_TEXT);
        $st->bindValue(':u', $now, SQLITE3_TEXT);
        $st->execute();
        nexrec_api_ok(['job_id' => $jid, 'hint' => 'python3 worker/nexrec-analyze.py --once']);
    }

    if ($action === 'scte224_ingest') {
        nexrec_require_roles(['admin', 'operator']);
        $iid = strtolower(trim((string) ($body['input_id'] ?? '')));
        if (!nexrec_valid_input_id($iid)) {
            nexrec_api_fail(400, 'input_id required');
        }
        $payload = $body['payload'] ?? $body['xml'] ?? $body['json'] ?? null;
        $summary = (string) ($body['summary'] ?? 'SCTE-224 ESAM message');
        $tStart = (string) ($body['t_start'] ?? nexrec_now_iso());
        $eid = nexrec_new_id('evt');
        $st = nexrec_db()->prepare(
            "INSERT INTO events (id,input_id,chunk_id,kind,subtype,t_start,t_end,pts,timecode,duration_s,payload_summary,payload_json,created_at)
             VALUES (:id,:i,NULL,'scte224','esam_http',:ts,NULL,NULL,NULL,NULL,:sum,:pj,:c)"
        );
        $st->bindValue(':id', $eid, SQLITE3_TEXT);
        $st->bindValue(':i', $iid, SQLITE3_TEXT);
        $st->bindValue(':ts', $tStart, SQLITE3_TEXT);
        $st->bindValue(':sum', $summary, SQLITE3_TEXT);
        $st->bindValue(':pj', is_string($payload) ? $payload : json_encode($payload), SQLITE3_TEXT);
        $st->bindValue(':c', nexrec_now_iso(), SQLITE3_TEXT);
        $st->execute();
        nexrec_api_ok(['event_id' => $eid]);
    }

    if ($action === 'metrics_list') {
        nexrec_require_roles(['admin', 'operator']);
        $hours = isset($_GET['hours']) ? (int) $_GET['hours'] : 24;
        if ($hours < 1) {
            $hours = 1;
        }
        if ($hours > 168) {
            $hours = 168;
        }
        $since = gmdate('Y-m-d\TH:i:s\Z', time() - ($hours * 3600));
        $host = [];
        $sdi = [];
        $pending = false;
        $loaded = false;
        for ($try = 0; $try < 2 && !$loaded; $try++) {
            $host = [];
            $sdi = [];
            try {
                $st = nexrec_db()->prepare('SELECT * FROM host_metrics WHERE sampled_at >= :s ORDER BY sampled_at ASC');
                $st->bindValue(':s', $since, SQLITE3_TEXT);
                $res = $st->execute();
                while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
                    $host[] = $row;
                }
                $st2 = nexrec_db()->prepare(
                    'SELECT sampled_at, input_id, sdi_lock, signal, format FROM sdi_lock_log WHERE sampled_at >= :s ORDER BY sampled_at ASC, input_id ASC'
                );
                $st2->bindValue(':s', $since, SQLITE3_TEXT);
                $res2 = $st2->execute();
                while ($res2 && ($row = $res2->fetchArray(SQLITE3_ASSOC))) {
                    $sdi[] = $row;
                }
                $loaded = true;
            } catch (Throwable $e) {
                if ($try === 0) {
                    nexrec_migrate();
                } else {
                    $pending = true;
                }
            }
        }
        $signals = [];
        $res3 = nexrec_db()->query('SELECT input_id, signal, sdi_lock, format, detail, seen_at FROM input_heartbeats ORDER BY input_id');
        if ($res3 !== false) {
            while ($row = $res3->fetchArray(SQLITE3_ASSOC)) {
                $signals[] = $row;
            }
        }
        nexrec_api_ok([
            'hours' => $hours,
            'since' => $since,
            'host' => $host,
            'sdi' => $sdi,
            'signals' => $signals,
            'pending' => $pending,
        ]);
    }

    if ($action === 'asruns_list') {
        nexrec_require_roles([]);
        nexrec_api_ok(['asruns' => nexrec_asrun_list()]);
    }

    if ($action === 'asrun_get') {
        nexrec_require_roles([]);
        $id = (string) ($_GET['id'] ?? $body['id'] ?? '');
        $row = nexrec_asrun_find($id);
        if ($row === null) {
            nexrec_api_fail(404, 'as-run not found');
        }
        nexrec_api_ok([
            'asrun' => [
                'id' => $row['id'],
                'name' => $row['name'],
                'filename' => $row['filename'],
                'channel' => $row['channel'],
                'broadcast_date' => $row['broadcast_date'],
                'timezone' => $row['timezone'],
                'day_start' => $row['day_start'],
                'event_count' => (int) $row['event_count'],
                'created_at' => $row['created_at'],
                'updated_at' => $row['updated_at'],
                'input_ids' => nexrec_asrun_input_ids($id),
            ],
            'events' => nexrec_asrun_events($id),
        ]);
    }

    if ($action === 'asrun_import') {
        nexrec_require_roles(['admin', 'operator']);
        $text = (string) ($body['text'] ?? '');
        if (trim($text) === '') {
            nexrec_api_fail(400, 'text required');
        }
        $ids = $body['input_ids'] ?? [];
        if (!is_array($ids)) {
            nexrec_api_fail(400, 'input_ids must be a list');
        }
        $saved = nexrec_asrun_import($text, [
            'filename' => (string) ($body['filename'] ?? ''),
            'name' => (string) ($body['name'] ?? ''),
            'timezone' => (string) ($body['timezone'] ?? 'America/New_York'),
            'day_start' => (string) ($body['day_start'] ?? '04:00:00'),
            'input_ids' => $ids,
        ]);
        nexrec_api_ok($saved);
    }

    if ($action === 'asrun_link') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        if (nexrec_asrun_find($id) === null) {
            nexrec_api_fail(404, 'as-run not found');
        }
        $ids = $body['input_ids'] ?? [];
        if (!is_array($ids)) {
            nexrec_api_fail(400, 'input_ids must be a list');
        }
        nexrec_asrun_link($id, $ids);
        nexrec_api_ok(['id' => $id, 'input_ids' => nexrec_asrun_input_ids($id)]);
    }

    if ($action === 'asrun_delete') {
        nexrec_require_roles(['admin', 'operator']);
        $id = (string) ($body['id'] ?? '');
        if (nexrec_asrun_find($id) === null) {
            nexrec_api_fail(404, 'as-run not found');
        }
        nexrec_asrun_delete($id);
        nexrec_api_ok(['deleted' => $id]);
    }

    if ($action === 'asrun_band') {
        nexrec_require_roles([]);
        $iid = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        $from = (string) ($_GET['t_from'] ?? $body['t_from'] ?? '');
        $to = (string) ($_GET['t_to'] ?? $body['t_to'] ?? '');
        if ($iid === '' || $from === '' || $to === '') {
            nexrec_api_fail(400, 'input_id, t_from, and t_to required');
        }
        nexrec_api_ok(['events' => nexrec_asrun_band($iid, $from, $to)]);
    }

    if ($action === 'analyze_jobs_list') {
        nexrec_require_roles(['admin', 'operator']);
        $out = [];
        $res = nexrec_db()->query('SELECT * FROM analyze_jobs ORDER BY created_at DESC LIMIT 50');
        if ($res !== false) {
            while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
                $out[] = $row;
            }
        }
        nexrec_api_ok(['jobs' => $out]);
    }

    if ($action === 'destinations_list') {
        nexrec_require_roles(['admin', 'operator']);
        $only = (string) ($_GET['enabled'] ?? $body['enabled'] ?? '') === '1';
        nexrec_api_ok(['destinations' => nexrec_destinations_public($only)]);
    }

    if ($action === 'destination_save') {
        $me = nexrec_require_roles(['admin']);
        $saved = nexrec_destination_save($body, (string) ($me['username'] ?? ''));
        nexrec_api_ok(['destination' => $saved]);
    }

    if ($action === 'destination_delete') {
        nexrec_require_roles(['admin']);
        nexrec_destination_delete((string) ($body['id'] ?? ''));
        nexrec_api_ok(['id' => (string) ($body['id'] ?? '')]);
    }

    if ($action === 'deliveries_list') {
        nexrec_require_roles(['admin', 'operator']);
        nexrec_api_ok(['deliveries' => nexrec_deliveries_public()]);
    }

    if ($action === 'delivery_cancel') {
        nexrec_require_roles(['admin', 'operator']);
        nexrec_delivery_cancel((string) ($body['id'] ?? ''));
        nexrec_api_ok(['id' => (string) ($body['id'] ?? '')]);
    }

    if ($action === 'delivery_retry') {
        nexrec_require_roles(['admin', 'operator']);
        nexrec_delivery_retry((string) ($body['id'] ?? ''));
        nexrec_api_ok(['id' => (string) ($body['id'] ?? '')]);
    }

    nexrec_api_fail(400, 'unknown action');
} catch (RuntimeException $e) {
    $msg = $e->getMessage();
    if ($msg === 'unauthorized') {
        nexrec_api_fail(401, $msg);
    }
    if ($msg === 'forbidden') {
        nexrec_api_fail(403, $msg);
    }
    nexrec_api_fail(500, $msg);
} catch (InvalidArgumentException $e) {
    nexrec_api_fail(400, $e->getMessage());
} catch (Throwable $e) {
    nexrec_api_fail(500, 'internal error');
}

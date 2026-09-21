<?php
/**
 * Recorder JSON API: inputs, chunks, exports, settings, nexclip, demo seed.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';

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

function nexrec_storage_dir(): string {
    nexrec_load_station_env();
    $p = getenv('NEXREC_STORAGE_DIR');
    if (is_string($p) && $p !== '') {
        return rtrim($p, '/');
    }
    return nexrec_data_dir() . '/storage';
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
        $floor = (string) (getenv('NEXREC_FREE_SPACE_FLOOR') ?: '50G');
        $storage = nexrec_storage_dir();
        $free = is_dir($storage) ? (int) disk_free_space($storage) : 0;
        nexrec_api_ok([
            'instance_id' => getenv('NEXREC_INSTANCE_ID') ?: null,
            'instance_name' => getenv('NEXREC_INSTANCE_NAME') ?: null,
            'mode' => getenv('NEXREC_DEPLOY_MODE') ?: 'standalone',
            'inputs' => $inputs,
            'chunks' => $chunks,
            'exports' => $exports,
            'storage_dir' => $storage,
            'free_bytes' => $free,
            'free_space_floor' => $floor,
            'segment_seconds' => (int) (getenv('NEXREC_SEGMENT_SECONDS') ?: 300),
            'max_inputs' => (int) (getenv('NEXREC_MAX_INPUTS') ?: 10),
        ]);
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

    if ($action === 'input_put') {
        nexrec_require_roles(['admin', 'operator']);
        $id = strtolower(trim((string) ($body['id'] ?? '')));
        if (!nexrec_valid_input_id($id)) {
            nexrec_api_fail(400, 'invalid id');
        }
        $max = (int) (getenv('NEXREC_MAX_INPUTS') ?: 10);
        $n = (int) nexrec_db()->querySingle('SELECT COUNT(*) FROM inputs');
        $exists = nexrec_db()->querySingle('SELECT COUNT(*) FROM inputs WHERE id=' . "'" . SQLite3::escapeString($id) . "'");
        if (!$exists && $n >= $max) {
            nexrec_api_fail(400, "max {$max} inputs");
        }
        $type = strtolower((string) ($body['source_type'] ?? 'rtsp'));
        $allowed = ['rtsp', 'srt', 'udp', 'tcp', 'rtp', 'decklink', 'testsrc'];
        if (!in_array($type, $allowed, true)) {
            nexrec_api_fail(400, 'invalid source_type');
        }
        $now = nexrec_now_iso();
        $st = nexrec_db()->prepare(
            'INSERT INTO inputs (
               id,name,source_type,url,decklink_device,decklink_format,enabled,live_transcode,copy_native,upconvert_1080i,
               video_bitrate,audio_bitrate,retention_days,preview_path,preview_enabled,
               feat_scte,feat_av_anomaly,feat_captions,feat_transcribe,feat_nielsen,feat_monitors,
               thresh_freeze_s,thresh_black_s,thresh_bars_s,transcribe_engine,
               created_at,updated_at)
             VALUES (
               :id,:name,:t,:url,:dd,:df,:en,:lt,:cn,:up,:vb,:ab,:rd,:pp,:pe,
               :scte,:ava,:cc,:tr,:ni,:mon,:tf,:tb,:tbar,:teng,:c,:u)
             ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, source_type=excluded.source_type, url=excluded.url,
               decklink_device=excluded.decklink_device, decklink_format=excluded.decklink_format,
               enabled=excluded.enabled, live_transcode=excluded.live_transcode,
               copy_native=excluded.copy_native, upconvert_1080i=excluded.upconvert_1080i,
               video_bitrate=excluded.video_bitrate, audio_bitrate=excluded.audio_bitrate,
               retention_days=excluded.retention_days, preview_path=excluded.preview_path,
               preview_enabled=excluded.preview_enabled,
               feat_scte=excluded.feat_scte, feat_av_anomaly=excluded.feat_av_anomaly,
               feat_captions=excluded.feat_captions, feat_transcribe=excluded.feat_transcribe,
               feat_nielsen=excluded.feat_nielsen, feat_monitors=excluded.feat_monitors,
               thresh_freeze_s=excluded.thresh_freeze_s, thresh_black_s=excluded.thresh_black_s,
               thresh_bars_s=excluded.thresh_bars_s, transcribe_engine=excluded.transcribe_engine,
               updated_at=excluded.updated_at'
        );
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $st->bindValue(':name', (string) ($body['name'] ?? $id), SQLITE3_TEXT);
        $st->bindValue(':t', $type, SQLITE3_TEXT);
        $st->bindValue(':url', $body['url'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':dd', $body['decklink_device'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':df', $body['decklink_format'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':en', !empty($body['enabled']) ? 1 : 0, SQLITE3_INTEGER);
        $st->bindValue(':lt', !empty($body['live_transcode']) ? 1 : 0, SQLITE3_INTEGER);
        $st->bindValue(':cn', !isset($body['copy_native']) || !empty($body['copy_native']) ? 1 : 0, SQLITE3_INTEGER);
        $st->bindValue(':up', !empty($body['upconvert_1080i']) ? 1 : 0, SQLITE3_INTEGER);
        $st->bindValue(':vb', $body['video_bitrate'] ?? null, SQLITE3_TEXT);
        $st->bindValue(':ab', $body['audio_bitrate'] ?? null, SQLITE3_TEXT);
        $st->bindValue(':rd', (int) ($body['retention_days'] ?? 28), SQLITE3_INTEGER);
        $st->bindValue(':pp', $body['preview_path'] ?? ('in' . min($n, 9)), SQLITE3_TEXT);
        $st->bindValue(':pe', !isset($body['preview_enabled']) || !empty($body['preview_enabled']) ? 1 : 0, SQLITE3_INTEGER);
        $st->bindValue(':scte', nexrec_flag($body, 'feat_scte'), SQLITE3_INTEGER);
        $st->bindValue(':ava', nexrec_flag($body, 'feat_av_anomaly'), SQLITE3_INTEGER);
        $st->bindValue(':cc', nexrec_flag($body, 'feat_captions'), SQLITE3_INTEGER);
        $st->bindValue(':tr', nexrec_flag($body, 'feat_transcribe'), SQLITE3_INTEGER);
        $st->bindValue(':ni', nexrec_flag($body, 'feat_nielsen'), SQLITE3_INTEGER);
        $st->bindValue(':mon', nexrec_flag($body, 'feat_monitors'), SQLITE3_INTEGER);
        $st->bindValue(':tf', nexrec_float_body($body, 'thresh_freeze_s', 2.0));
        $st->bindValue(':tb', nexrec_float_body($body, 'thresh_black_s', 2.0));
        $st->bindValue(':tbar', nexrec_float_body($body, 'thresh_bars_s', 5.0));
        $st->bindValue(':teng', (string) ($body['transcribe_engine'] ?? ''), SQLITE3_TEXT);
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
        foreach (['events', 'captions', 'loudness_samples', 'analyze_jobs', 'chunks'] as $tbl) {
            $st = $db->prepare("DELETE FROM {$tbl} WHERE input_id=:i");
            $st->bindValue(':i', $id, SQLITE3_TEXT);
            $st->execute();
        }
        @$db->exec("DELETE FROM captions_fts WHERE input_id='" . SQLite3::escapeString($id) . "'");
        $st = $db->prepare('DELETE FROM inputs WHERE id=:i');
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $st->execute();
        nexrec_api_ok(['deleted' => $id]);
    }

    if ($action === 'chunks_list') {
        nexrec_require_roles([]);
        $iid = (string) ($_GET['input_id'] ?? $body['input_id'] ?? '');
        $sql = 'SELECT * FROM chunks WHERE ready=1 AND orphan=0';
        $args = [];
        if ($iid !== '') {
            $sql .= ' AND input_id = :i';
        }
        $sql .= ' ORDER BY start_at DESC LIMIT 500';
        $st = nexrec_db()->prepare($sql);
        if ($iid !== '') {
            $st->bindValue(':i', $iid, SQLITE3_TEXT);
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
        $days = (int) (getenv('NEXREC_EXPORT_RETENTION_DAYS') ?: 15);
        $expId = nexrec_new_id('exp');
        $protected = !empty($body['protected']) ? 1 : 0;
        $expires = $protected ? null : gmdate('Y-m-d\TH:i:s\Z', time() + $days * 86400);
        $st = nexrec_db()->prepare(
            'INSERT INTO exports (id,status,input_ids,t_in,t_out,quality,scope,path,size_bytes,protected,error,created_by,created_at,expires_at,nexclip_schedule_id)
             VALUES (:id,"queued",:ids,:tin,:tout,:q,:sc,NULL,NULL,:p,NULL,:by,:c,:e,NULL)'
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
        $st->execute();
        nexrec_api_ok(['export_id' => $expId]);
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

    if ($action === 'chunk_media') {
        nexrec_require_roles([]);
        $id = (string) ($_GET['id'] ?? '');
        $st = nexrec_db()->prepare('SELECT path FROM chunks WHERE id=:i AND ready=1');
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        if (!$row || !is_file($row['path'])) {
            nexrec_api_fail(404, 'not found');
        }
        header('Content-Type: video/mp4');
        header('Accept-Ranges: bytes');
        header('Cache-Control: private, max-age=60');
        readfile($row['path']);
        exit;
    }

    if ($action === 'export_file') {
        nexrec_require_roles([]);
        $id = (string) ($_GET['id'] ?? '');
        $st = nexrec_db()->prepare("SELECT path FROM exports WHERE id=:i AND status='done'");
        $st->bindValue(':i', $id, SQLITE3_TEXT);
        $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
        if (!$row || !is_file((string) $row['path'])) {
            nexrec_api_fail(404, 'not found');
        }
        header('Content-Type: video/mp4');
        header('Content-Disposition: attachment; filename="' . basename((string) $row['path']) . '"');
        readfile((string) $row['path']);
        exit;
    }

    if ($action === 'settings_get') {
        nexrec_require_roles(['admin', 'operator']);
        $keys = [
            'NEXREC_DEPLOY_MODE', 'NEXREC_INSTANCE_ID', 'NEXREC_INSTANCE_NAME',
            'NEXREC_STORAGE_DIR', 'NEXREC_FREE_SPACE_FLOOR', 'NEXREC_SEGMENT_SECONDS',
            'NEXREC_NATIVE_RETENTION_DAYS', 'NEXREC_EXPORT_RETENTION_DAYS',
            'NEXREC_BROADCAST_VIDEO_BITRATE', 'NEXREC_LDAP_ENABLED',
            'NEXREC_NEXAPP_ENABLED', 'NEXREC_NEXCLIP_ENABLED',
            'NEXAPP_ISSUER', 'NEXCLIP_BASE_URL', 'NEXREC_PREVIEW_ENABLED',
            'NEXREC_TRANSCRIBE_ENGINE',
        ];
        $out = [];
        foreach ($keys as $k) {
            $v = getenv($k);
            $out[$k] = is_string($v) ? $v : '';
        }
        nexrec_api_ok(['settings' => $out]);
    }

    if ($action === 'demo_seed') {
        nexrec_require_roles(['admin', 'operator']);
        $now = time();
        $id = 'demo';
        $exists = nexrec_db()->querySingle("SELECT COUNT(*) FROM inputs WHERE id='demo'");
        if (!$exists) {
            $st = nexrec_db()->prepare(
                'INSERT INTO inputs (id,name,source_type,url,decklink_device,decklink_format,enabled,live_transcode,copy_native,upconvert_1080i,video_bitrate,audio_bitrate,retention_days,preview_path,preview_enabled,created_at,updated_at)
                 VALUES ("demo","Demo color bars","testsrc","", "", "", 1, 1, 0, 0, "4M", "128k", 28, "in0", 1, :c, :u)'
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
                    'INSERT INTO chunks (id,input_id,path,kind,start_at,end_at,duration_s,size_bytes,width,height,fps,interlaced,codec,timecode_start,ready,orphan,created_at)
                     VALUES (:id,"demo",:p,"native",:s,:e,300,0,1280,720,30,0,"h264",NULL,1,0,:c)'
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
        $ftsQuery = '"' . str_replace(['"', "'"], '', $q) . '"';
        $ftsOk = true;
        try {
            $sql = 'SELECT id, input_id, kind, t_start, speaker, text FROM captions_fts WHERE captions_fts MATCH :q';
            if ($iid !== '') {
                $sql .= ' AND input_id = :i';
            }
            $sql .= ' LIMIT :n';
            $st = nexrec_db()->prepare($sql);
            if ($st === false) {
                throw new RuntimeException('no fts');
            }
            $st->bindValue(':q', $ftsQuery, SQLITE3_TEXT);
            if ($iid !== '') {
                $st->bindValue(':i', $iid, SQLITE3_TEXT);
            }
            $st->bindValue(':n', $limit, SQLITE3_INTEGER);
            $res = $st->execute();
            while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
                $hits[] = $row;
            }
            $engine = 'fts5';
        } catch (Throwable $e) {
            $ftsOk = false;
        }
        if (!$ftsOk || $engine !== 'fts5') {
            $sql = 'SELECT id, input_id, kind, service, speaker, t_start, t_end, text FROM captions WHERE text LIKE :q';
            if ($iid !== '') {
                $sql .= ' AND input_id = :i';
            }
            $sql .= ' ORDER BY t_start DESC LIMIT :n';
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
            'INSERT INTO analyze_jobs (id,status,kind,input_id,export_id,t_in,t_out,path,error,result_json,created_at,updated_at)
             VALUES (:id,"queued","loudness",:i,NULL,:tin,:tout,NULL,NULL,NULL,:c,:u)'
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
            'INSERT INTO events (id,input_id,chunk_id,kind,subtype,t_start,t_end,pts,timecode,duration_s,payload_summary,payload_json,created_at)
             VALUES (:id,:i,NULL,"scte224","esam_http",:ts,NULL,NULL,NULL,NULL,:sum,:pj,:c)'
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
} catch (Throwable $e) {
    nexrec_api_fail(500, 'internal error');
}

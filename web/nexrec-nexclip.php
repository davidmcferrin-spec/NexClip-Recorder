<?php
/**
 * Inbound NexClip webhook (API key). Schedule push + export status.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';

function nexrec_nexclip_fail(int $status, string $message): never {
    header('Content-Type: application/json');
    http_response_code($status);
    echo json_encode(['ok' => false, 'error' => $message]);
    exit;
}

function nexrec_nexclip_key_ok(): bool {
    nexrec_load_station_env();
    $key = (string) (getenv('NEXCLIP_API_KEY') ?: getenv('NEXREC_API_KEY') ?: '');
    if ($key === '') {
        return false;
    }
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (is_string($header) && preg_match('/^Bearer\s+(\S+)/i', $header, $m) === 1) {
        return hash_equals($key, $m[1]);
    }
    $h = $_SERVER['HTTP_X_NEXCLIP_KEY'] ?? '';
    return is_string($h) && hash_equals($key, $h);
}

if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
    return;
}

if (!nexrec_nexclip_key_ok()) {
    nexrec_nexclip_fail(401, 'unauthorized');
}

nexrec_migrate();
$raw = file_get_contents('php://input');
$body = is_string($raw) ? json_decode($raw, true) : [];
if (!is_array($body)) {
    $body = [];
}
$action = $_GET['action'] ?? ($body['action'] ?? 'schedule_push');

if ($action === 'export_status') {
    $id = (string) ($body['export_id'] ?? $_GET['export_id'] ?? '');
    $st = nexrec_db()->prepare('SELECT * FROM exports WHERE id=:i');
    $st->bindValue(':i', $id, SQLITE3_TEXT);
    $row = $st->execute()->fetchArray(SQLITE3_ASSOC);
    if (!$row) {
        nexrec_nexclip_fail(404, 'not found');
    }
    echo json_encode(['ok' => true, 'export' => $row], JSON_UNESCAPED_SLASHES);
    exit;
}

if ($action === 'schedule_push') {
    $events = $body['events'] ?? null;
    if ($events === null && isset($body['id'])) {
        $events = [$body];
    }
    if (!is_array($events)) {
        nexrec_nexclip_fail(400, 'events required');
    }
    $stub = nexrec_data_dir() . '/nexclip-push.json';
    if (!is_dir(nexrec_data_dir())) {
        mkdir(nexrec_data_dir(), 0770, true);
    }
    file_put_contents($stub, json_encode(['events' => $events], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
    // Worker nexrec-nexclip.py --env with NEXCLIP_SCHEDULE_STUB will enqueue.
    // Also cache rows immediately so the UI can show them.
    foreach ($events as $ev) {
        if (!is_array($ev) || empty($ev['id'])) {
            continue;
        }
        $st = nexrec_db()->prepare(
            'INSERT INTO nexclip_events (id,input_id,title,start_at,end_at,payload,export_id,status,fetched_at)
             VALUES (:id,:iid,:t,:s,:e,:p,NULL,"cached",:f)
             ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at'
        );
        $st->bindValue(':id', (string) $ev['id'], SQLITE3_TEXT);
        $st->bindValue(':iid', $ev['input_id'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':t', $ev['title'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':s', $ev['start_at'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':e', $ev['end_at'] ?? '', SQLITE3_TEXT);
        $st->bindValue(':p', json_encode($ev), SQLITE3_TEXT);
        $st->bindValue(':f', nexrec_now_iso(), SQLITE3_TEXT);
        $st->execute();
    }
    echo json_encode(['ok' => true, 'cached' => count($events)]);
    exit;
}

nexrec_nexclip_fail(400, 'unknown action');

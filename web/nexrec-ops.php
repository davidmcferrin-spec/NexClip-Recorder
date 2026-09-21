<?php
/**
 * Lightweight ops: worker unit names + storage. No unsanitized sudo FFmpeg.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';

if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
    return;
}

header('Content-Type: application/json');
header('Cache-Control: no-store');

try {
    nexrec_require_roles(['admin', 'operator']);
} catch (RuntimeException $e) {
    http_response_code($e->getMessage() === 'forbidden' ? 403 : 401);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()]);
    exit;
}

$action = $_GET['action'] ?? 'units';
$units = ['nexrec-export.service', 'nexrec-cleanup.timer', 'mediamtx.service'];
$res = nexrec_db()->query('SELECT id FROM inputs WHERE enabled=1');
if ($res !== false) {
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $units[] = 'nexrec-record@' . $row['id'] . '.service';
        $units[] = 'nexrec-preview@' . $row['id'] . '.service';
    }
}

$out = [];
foreach ($units as $u) {
    $state = 'unknown';
    $cmd = ['/bin/systemctl', 'is-active', '--quiet', $u];
    // Best-effort; demo hosts may lack systemd.
    $rc = 1;
    if (is_executable('/bin/systemctl')) {
        $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes, null, null, ['bypass_shell' => true]);
        if (is_resource($p)) {
            fclose($pipes[1]);
            fclose($pipes[2]);
            $rc = proc_close($p);
            $state = $rc === 0 ? 'active' : 'inactive';
        }
    }
    $out[] = ['unit' => $u, 'state' => $state];
}

echo json_encode(['ok' => true, 'units' => $out], JSON_UNESCAPED_SLASHES);

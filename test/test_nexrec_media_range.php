#!/usr/bin/env php
<?php
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-api.php';

function nexrec_expect(bool $ok, string $message): void {
    if (!$ok) {
        fwrite(STDERR, $message . "\n");
        exit(1);
    }
}

$full = nexrec_media_plan('', 100, 'video/mp4');
nexrec_expect($full['status'] === 200, 'missing range should be 200');
nexrec_expect($full['start'] === 0 && $full['length'] === 100, 'missing range should be the whole file');
nexrec_expect($full['headers']['Content-Length'] === '100', 'full response needs Content-Length');
nexrec_expect(!isset($full['headers']['Content-Range']), 'full response has no Content-Range');
nexrec_expect($full['headers']['Accept-Ranges'] === 'bytes', 'full response still advertises ranges');

$open = nexrec_media_plan('bytes=0-', 100, 'video/mp4');
nexrec_expect($open['status'] === 206, 'bytes=0- should be 206');
nexrec_expect($open['start'] === 0 && $open['length'] === 100, 'bytes=0- covers the file');
nexrec_expect($open['headers']['Content-Range'] === 'bytes 0-99/100', 'bytes=0- content range');
nexrec_expect($open['headers']['Content-Length'] === '100', 'bytes=0- length');

$slice = nexrec_media_plan('bytes=10-19', 100, 'video/mp4');
nexrec_expect($slice['status'] === 206 && $slice['start'] === 10 && $slice['length'] === 10, 'mid-file slice');
nexrec_expect($slice['headers']['Content-Range'] === 'bytes 10-19/100', 'mid-file content range');

$tail = nexrec_media_plan('bytes=90-', 100, 'video/mp4');
nexrec_expect($tail['status'] === 206 && $tail['start'] === 90 && $tail['length'] === 10, 'open end');

$suffix = nexrec_media_plan('bytes=-10', 100, 'video/mp4');
nexrec_expect($suffix['status'] === 206 && $suffix['start'] === 90 && $suffix['length'] === 10, 'suffix range');

$past = nexrec_media_plan('bytes=100-', 100, 'video/mp4');
nexrec_expect($past['status'] === 416 && $past['length'] === 0, 'start past end is 416');
nexrec_expect($past['headers']['Content-Range'] === 'bytes */100', '416 advertises the size');

$backwards = nexrec_media_plan('bytes=50-40', 100, 'video/mp4');
nexrec_expect($backwards['status'] === 416, 'end before start is 416');

$multi = nexrec_media_plan('bytes=0-1,2-3', 100, 'video/mp4');
nexrec_expect($multi['status'] === 200 && $multi['length'] === 100, 'multipart range falls back to the whole file');

$download = nexrec_media_plan('', 4, 'video/mp4', "clip\"\r\n.mp4", 'private, no-store');
nexrec_expect($download['headers']['Content-Disposition'] === 'attachment; filename="clip.mp4"', 'download name is stripped');
nexrec_expect($download['headers']['Cache-Control'] === 'private, no-store', 'export cache control');

$dir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'nexrec-sess-' . bin2hex(random_bytes(4));
mkdir($dir);
session_save_path($dir);
session_id('nexrecmediatest');
session_start();
$_SESSION['user_id'] = 'held';
nexrec_release_session();
nexrec_expect(session_status() !== PHP_SESSION_ACTIVE, 'session must be closed before the file is sent');

$file = tempnam(sys_get_temp_dir(), 'nexrec-media-');
if ($file === false) {
    fwrite(STDERR, "temp file\n");
    exit(1);
}
file_put_contents($file, 'abcdefghijklmnopqrstuvwxyz');
$runner = tempnam(sys_get_temp_dir(), 'nexrec-send-');
if ($runner === false) {
    fwrite(STDERR, "runner\n");
    exit(1);
}
$runnerPhp = $runner . '.php';
rename($runner, $runnerPhp);
$api = dirname(__DIR__) . '/web/nexrec-api.php';
file_put_contents($runnerPhp, "<?php\ndeclare(strict_types=1);\n\$_SERVER['HTTP_RANGE'] = \$argv[2];\nrequire " . var_export($api, true) . ";\nnexrec_send_media_file(\$argv[1], 'video/mp4');\n");
$php = PHP_BINARY;
$cmd = escapeshellarg($php) . ' ' . escapeshellarg($runnerPhp) . ' ' . escapeshellarg($file) . ' ' . escapeshellarg('bytes=2-5');
$out = shell_exec($cmd);
nexrec_expect($out === 'cdef', 'send writes only the requested bytes, got ' . var_export($out, true));

@unlink($file);
@unlink($runnerPhp);
foreach (glob($dir . DIRECTORY_SEPARATOR . '*') ?: [] as $sess) {
    @unlink($sess);
}
@rmdir($dir);

echo "test_nexrec_media_range.php ok\n";

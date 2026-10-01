#!/usr/bin/env php
<?php
declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/nexrec-share-' . bin2hex(random_bytes(4));
mkdir($tmp, 0700, true);
$storage = $tmp . '/storage';
mkdir($storage . '/exports', 0700, true);

$root = dirname(__DIR__);
$export = (string) file_get_contents($root . '/web/pages/export.html');
foreach (['id="lkfs-panel"', 'measure-lkfs', 'id="scope"', 'id="quality"'] as $needle) {
    if (str_contains($export, $needle)) {
        fwrite(STDERR, "export page still has {$needle}\n");
        exit(1);
    }
}
foreach (['id="export-dialog"', 'id="share-dialog"', 'This stream', 'All visible streams', 'Choose streams', 'Require sign-in to download', 'Share as URL', 'Copy to clipboard', 'id="export-share-copy"', 'id="export-auth-wrap"'] as $needle) {
    if (!str_contains($export, $needle)) {
        fwrite(STDERR, "export page missing {$needle}\n");
        exit(1);
    }
}
$order = ['>Streams<', 'export-quality', 'export-title', 'export-description', 'export-share-as-url', 'export-auth-wrap', 'export-send-wrap', 'export-submit'];
$at = -1;
foreach ($order as $marker) {
    $pos = strpos($export, $marker);
    if ($pos === false || $pos <= $at) {
        fwrite(STDERR, "export dialog fields out of order at {$marker}\n");
        exit(1);
    }
    $at = $pos;
}
if (!is_file($root . '/web/pages/share.html')) {
    fwrite(STDERR, "share page missing\n");
    exit(1);
}

putenv('NEXREC_DATA_DIR=' . $tmp);
putenv('NEXREC_DB=' . $tmp . '/nexrec.db');
putenv('NEXREC_STORAGE_DIR=' . $storage);
putenv('NEXREC_AUTH_HTTP');

require dirname(__DIR__) . '/web/nexrec-api.php';
nexrec_migrate();

$root = dirname(__DIR__);
if (nexrec_share_title("  Morning\nShow  ") !== 'Morning Show') {
    fwrite(STDERR, "title was not flattened\n");
    exit(1);
}
if (nexrec_share_title("bad\x00name") !== 'badname') {
    fwrite(STDERR, "title kept a control character\n");
    exit(1);
}
if (strlen(nexrec_share_title(str_repeat('a', 300))) !== 200) {
    fwrite(STDERR, "title was not limited\n");
    exit(1);
}

$db = nexrec_db();
$db->exec(
    "INSERT INTO exports (id, input_ids, t_in, t_out, created_at)
     VALUES ('exp_old', '[\"cam\"]', '2026-09-21T15:00:00Z', '2026-09-21T15:01:00Z', '2026-09-21T15:01:00Z')"
);
nexrec_backfill_share_tokens();
$st = $db->prepare('SELECT share_token, auth_required, title FROM exports WHERE id=:id');
$st->bindValue(':id', 'exp_old', SQLITE3_TEXT);
$old = nexrec_row($st->execute());
if ($old === null || preg_match('/^[a-f0-9]{32}$/', (string) $old['share_token']) !== 1) {
    fwrite(STDERR, "backfill token\n");
    exit(1);
}
if ((int) $old['auth_required'] !== 0 || (string) $old['title'] !== '') {
    fwrite(STDERR, "backfill defaults\n");
    exit(1);
}

$token = str_repeat('cd', 16);
$single = $storage . '/exports/exp_one.mp4';
file_put_contents($single, 'one');
file_put_contents($storage . '/exports/exp_two_cam.mp4', 'cam');
file_put_contents($storage . '/exports/exp_two_studio.mp4', 'studio');
$db->exec("INSERT INTO inputs (id,name,source_type,enabled,live_transcode,copy_native,upconvert_1080i,retention_days,preview_enabled,created_at,updated_at)
  VALUES ('cam','Camera','rtsp',1,0,1,0,28,1,'2026-09-21T00:00:00Z','2026-09-21T00:00:00Z')");

$one = [
    'id' => 'exp_one',
    'input_ids' => '["cam"]',
    'status' => 'done',
    'path' => '',
    'title' => 'Morning',
    'description' => "First line\nSecond",
    'quality' => 'full',
    't_in' => '2026-09-21T15:00:00Z',
    't_out' => '2026-09-21T15:05:00Z',
    'auth_required' => 0,
    'share_token' => $token,
];
$files = nexrec_export_output_files($one);
if (count($files) !== 1 || !is_file($files[0]['path'])) {
    fwrite(STDERR, "single export file path\n");
    exit(1);
}
if (!nexrec_share_open($one)) {
    fwrite(STDERR, "public share was closed\n");
    exit(1);
}
$payload = nexrec_share_payload($one);
if (($payload['files'][0]['name'] ?? '') !== 'Camera' || empty($payload['files'][0]['ready'])) {
    fwrite(STDERR, "single share payload\n");
    exit(1);
}
if (($payload['files'][0]['href'] ?? '') !== '/api/share/' . $token . '/cam') {
    fwrite(STDERR, "single share href\n");
    exit(1);
}
if (isset($payload['path']) || isset($payload['files'][0]['path'])) {
    fwrite(STDERR, "share payload leaked a path\n");
    exit(1);
}
$closed = $one;
$closed['auth_required'] = 1;
if (nexrec_share_open($closed)) {
    fwrite(STDERR, "sign-in share opened without a session\n");
    exit(1);
}

$many = $one;
$many['id'] = 'exp_two';
$many['input_ids'] = '["cam","studio"]';
$many['auth_required'] = 0;
$listed = nexrec_share_payload($many);
if (count($listed['files']) !== 2 || empty($listed['files'][1]['ready'])) {
    fwrite(STDERR, "multi share files\n");
    exit(1);
}
if (($listed['files'][1]['href'] ?? '') !== '/api/share/' . $token . '/studio') {
    fwrite(STDERR, "multi share href\n");
    exit(1);
}
$named = $many;
$named['path'] = $storage . '/exports/Charlie_20261001_145122-150122_a8c98ff5ed73.mp4';
$named['file_names'] = json_encode([
    'cam' => 'Charlie_20261001_145122-150122_a8c98ff5ed73.mp4',
    'studio' => 'Delta_20261001_145122-150122_a8c98ff5ed73.mp4',
], JSON_UNESCAPED_SLASHES);
$resolved = nexrec_export_output_files($named);
$resolvedNames = array_map(static fn (array $file): string => basename((string) $file['path']), $resolved);
if ($resolvedNames !== [
    'Charlie_20261001_145122-150122_a8c98ff5ed73.mp4',
    'Delta_20261001_145122-150122_a8c98ff5ed73.mp4',
]) {
    fwrite(STDERR, "readable export names were not used\n");
    exit(1);
}

echo "test_nexrec_share.php ok\n";

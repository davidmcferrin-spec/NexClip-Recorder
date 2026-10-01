<?php
/**
 * Send-to cipher matches the worker, and list payloads do not contain the password.
 */
declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/nexrec-deliver-' . bin2hex(random_bytes(4));
mkdir($tmp, 0700, true);
putenv('NEXREC_DATA_DIR=' . $tmp);
putenv('NEXREC_DB=' . $tmp . '/nexrec.db');
putenv('NEXREC_STORAGE_DIR=' . $tmp . '/storage');
putenv('NEXREC_DEST_KEY=test-key');
putenv('NEXREC_AUTH_HTTP');

require dirname(__DIR__) . '/web/nexrec-secret.php';

function fail(string $msg): never {
    fwrite(STDERR, $msg . "\n");
    exit(1);
}

$nonce = hex2bin('000102030405060708090a0b0c0d0e0f');
$blob = nexrec_secret_encrypt('s3cret', 'test-key', $nonce);
$vector = 'AAECAwQFBgcICQoLDA0OD8BTQqVNcWF7m5nzQgMo6olAONGYcCjaYW+bwfwhT2EOxKa6dI5D';
if ($blob !== $vector) {
    fail('cipher does not match the worker');
}
if (nexrec_secret_decrypt($blob, 'test-key') !== 's3cret') {
    fail('decrypt failed');
}
if (str_contains($blob, 's3cret')) {
    fail('cipher contains the password');
}

$page = (string) file_get_contents(dirname(__DIR__) . '/web/pages/transfers.html');
foreach (['Transfer queue', 'deliveries_list', 'destination_save'] as $needle) {
    if (!str_contains($page, $needle)) {
        fail('transfers page missing ' . $needle);
    }
}
$export = (string) file_get_contents(dirname(__DIR__) . '/web/pages/export.html');
if (!str_contains($export, 'destination_ids') || !str_contains($export, 'Send to')) {
    fail('export dialog missing send-to');
}

if (!extension_loaded('pdo_pgsql')) {
    echo "test_nexrec_deliver.php ok (cipher only, no pdo_pgsql)\n";
    exit(0);
}

require dirname(__DIR__) . '/web/nexrec-api.php';
nexrec_migrate();

$saved = nexrec_destination_save([
    'name' => 'Edit bay',
    'protocol' => 'sftp',
    'host' => 'files.example',
    'username' => 'edit',
    'secret' => 's3cret',
    'remote_prefix' => 'incoming',
    'enabled' => 1,
], 'admin');
$archive = nexrec_destination_save([
    'name' => 'Archive',
    'protocol' => 's3',
    'host' => 'minio.local',
    'username' => 'key',
    'secret' => 's3cret',
    'remote_prefix' => 'shows',
    'extra' => ['bucket' => 'media', 'region' => 'us-east-1', 'path_style' => 1],
], 'admin');
$encoded = json_encode([$saved, nexrec_destinations_public(false)]);
if ($encoded === false || str_contains($encoded, 's3cret') || str_contains($encoded, 'secret_cipher')) {
    fail('destination list leaked a password');
}
if (empty($saved['has_secret']) || ($saved['protocol'] ?? '') !== 'sftp') {
    fail('saved destination was not public');
}

$now = gmdate('Y-m-d\TH:i:s\Z');
$st = nexrec_db()->prepare(
    "INSERT INTO exports (id,status,input_ids,t_in,t_out,created_at)
     VALUES ('exp_abc','done','[\"cam\"]',:t,:t,:t)"
);
$st->bindValue(':t', $now, SQLITE3_TEXT);
$st->execute();
nexrec_deliver_attach('exp_abc', [$saved['id'], $archive['id'], $saved['id']]);
$rows = [];
$res = nexrec_db()->query("SELECT status, destination_id FROM deliveries WHERE export_id='exp_abc' ORDER BY destination_id");
while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
    $rows[] = $row;
}
if (count($rows) !== 2) {
    fail('one export should have one waiting row per destination');
}
foreach ($rows as $row) {
    if ((string) $row['status'] !== 'waiting') {
        fail('delivery was queued before the export finished');
    }
}

$up = nexrec_db()->prepare("UPDATE exports SET status='queued' WHERE id='exp_abc'");
$up->execute();
nexrec_deliver_hold('exp_abc');
$still = nexrec_db()->querySingle("SELECT COUNT(*) FROM deliveries WHERE export_id='exp_abc' AND status='waiting'");
if ((int) $still !== 2) {
    fail('re-run should park every destination until the new export finishes');
}

echo "test_nexrec_deliver.php ok\n";

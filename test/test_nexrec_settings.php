#!/usr/bin/env php
<?php
/**
 * app_settings: seed once from env, DB wins, validation, no secret storage.
 */
declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/nexrec-settings-' . bin2hex(random_bytes(4));
mkdir($tmp, 0700, true);
file_put_contents($tmp . '/empty.env', "# bootstrap only\n");
putenv('NEXREC_ENV_FILE=' . $tmp . '/empty.env');
putenv('NEXREC_DATA_DIR=' . $tmp);
putenv('NEXREC_DB=' . $tmp . '/nexrec.db');
putenv('NEXREC_FREE_SPACE_FLOOR=12G');
putenv('NEXREC_INSTANCE_NAME=From Env');
putenv('NEXREC_ENV_OVERRIDES');
putenv('NEXAPP_LAUNCH_SECRET=super-secret');
putenv('NEXREC_LDAP_ENABLED=0');

require dirname(__DIR__) . '/web/nexrec-auth-lib.php';

function fail(string $msg): never {
    fwrite(STDERR, $msg . "\n");
    exit(1);
}

nexrec_migrate();
$first = nexrec_settings_seed();
if ($first < 10) {
    fail("expected a full seed, got {$first}");
}
if (nexrec_setting('storage.free_space_floor') !== '12G') {
    fail('seed should copy free-space floor from env, got ' . nexrec_setting('storage.free_space_floor'));
}
if (nexrec_setting('station.display_name') !== 'From Env') {
    fail('seed display name');
}

$updated = nexrec_settings_put([
    'storage.free_space_floor' => '8G',
    'nexapp.service_id' => 'nexclip-recorder-ctl2',
    'nexapp.mode' => 'wan',
    'ffmpeg.segment_seconds' => '300',
    'retention.raw_days' => '21',
], 'tester');
if (!in_array('storage.free_space_floor', $updated, true)) {
    fail('patch did not report floor');
}
if (nexrec_setting('storage.free_space_floor') !== '8G') {
    fail('DB value should win after patch');
}
if (nexrec_setting('nexapp.mode') !== 'wan') {
    fail('mode wan');
}

$again = nexrec_settings_seed();
if ($again !== 0) {
    fail('second seed must not insert over existing rows');
}
if (nexrec_setting('storage.free_space_floor') !== '8G') {
    fail('second seed overwrote DB');
}

putenv('NEXREC_FREE_SPACE_FLOOR=99G');
if (nexrec_setting('storage.free_space_floor') !== '8G') {
    fail('env change must not override DB');
}
putenv('NEXREC_ENV_OVERRIDES=1');
if (nexrec_setting('storage.free_space_floor') !== '99G') {
    fail('break-glass env override');
}
putenv('NEXREC_ENV_OVERRIDES');

try {
    nexrec_settings_put(['storage.recordings' => '/tmp/foo;rm -rf /']);
    fail('path injection should fail');
} catch (InvalidArgumentException $e) {
    // expected
}
try {
    nexrec_settings_put(['no.such' => 'x']);
    fail('unknown key should fail');
} catch (InvalidArgumentException $e) {
    // expected
}
try {
    nexrec_settings_put(['nexapp.wan_redeem_secret_ref' => 'super-secret']);
    fail('raw secret must not be accepted as a ref');
} catch (InvalidArgumentException $e) {
    // expected
}

$pub = nexrec_settings_public();
$blob = json_encode($pub);
if ($blob === false || str_contains($blob, 'super-secret')) {
    fail('public settings leaked a secret');
}
$sawSecret = false;
foreach ($pub['secrets'] as $s) {
    if ($s['key'] === 'NEXAPP_LAUNCH_SECRET') {
        $sawSecret = true;
        if (empty($s['set'])) {
            fail('launch secret should be marked set without revealing it');
        }
    }
}
if (!$sawSecret) {
    fail('secret inventory missing launch secret');
}
if (($pub['settings']['nexapp.service_id'] ?? '') !== 'nexclip-recorder-ctl2') {
    fail('readback service id');
}
if ((int) ($pub['settings']['retention.raw_days'] ?? 0) !== 21) {
    fail('readback retention');
}

$pem = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n-----END PUBLIC KEY-----\n";
nexrec_settings_put(['nexapp.public_key_pem' => $pem]);
$path = nexrec_setting('nexapp.public_key_path');
if (!is_file($path) || !str_contains((string) file_get_contents($path), 'BEGIN PUBLIC KEY')) {
    fail('PEM should be written to a file, not only the DB');
}
$row = nexrec_db()->querySingle("SELECT value FROM app_settings WHERE key='nexapp.public_key_pem'");
if ($row) {
    fail('PEM key name must not be stored');
}

echo "test_nexrec_settings.php ok\n";
exit(0);

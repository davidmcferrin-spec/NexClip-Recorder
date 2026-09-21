#!/usr/bin/env php
<?php
/**
 * Local auth: seed, login verify, reject bad password, roles.
 */
declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/nexrec-auth-' . bin2hex(random_bytes(4));
mkdir($tmp, 0700, true);
putenv('NEXREC_DATA_DIR=' . $tmp);
putenv('NEXREC_DB=' . $tmp . '/nexrec.db');
putenv('NEXREC_ADMIN_USER=admin');
putenv('NEXREC_ADMIN_PASSWORD=password');
putenv('NEXREC_LDAP_ENABLED=0');

require dirname(__DIR__) . '/web/nexrec-auth-lib.php';

nexrec_migrate();
nexrec_seed_admin();
$n = (int) nexrec_db()->querySingle('SELECT COUNT(*) FROM users');
if ($n !== 1) {
    fwrite(STDERR, "expected 1 user, got {$n}\n");
    exit(1);
}
$ok = nexrec_user_verify_local('admin', 'password');
if ($ok === null) {
    fwrite(STDERR, "admin/password should verify\n");
    exit(1);
}
$bad = nexrec_user_verify_local('admin', 'nope');
if ($bad !== null) {
    fwrite(STDERR, "bad password should fail\n");
    exit(1);
}
$op = nexrec_user_create([
    'username' => 'tapeop',
    'password' => 'tapeop12',
    'role' => 'operator',
]);
if ($op['role'] !== 'operator') {
    fwrite(STDERR, "operator role\n");
    exit(1);
}

// NexAPP stub access
$stub = $tmp . '/access.json';
file_put_contents($stub, json_encode([
    'ok' => true,
    'status' => 200,
    'sub' => 'user-1',
    'email' => 'user@example.internal',
    'name' => 'User One',
    'role' => 'admin',
    'allowed_instances' => ['recorder-01'],
]));
putenv('NEXREC_NEXAPP_ACCESS_STUB=' . $stub);
putenv('NEXREC_INSTANCE_ID=recorder-01');
require_once dirname(__DIR__) . '/web/nexrec-nexapp.php';
$acc = nexrec_nexapp_check_access('ignored');
if (empty($acc['ok'])) {
    fwrite(STDERR, "stub access should be ok\n");
    exit(1);
}
putenv('NEXREC_INSTANCE_ID=recorder-99');
$acc2 = nexrec_nexapp_check_access('ignored');
if (!empty($acc2['ok']) || ($acc2['error'] ?? '') !== 'instance_not_granted') {
    fwrite(STDERR, "wrong instance should 403\n");
    exit(1);
}

echo "test_nexrec_auth.php ok\n";
exit(0);

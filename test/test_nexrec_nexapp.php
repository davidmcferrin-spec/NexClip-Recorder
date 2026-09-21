#!/usr/bin/env php
<?php
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-nexapp.php';

$jwt = 'not.a.jwt';
try {
    nexrec_nexapp_verify_jwt($jwt);
    fwrite(STDERR, "malformed jwt should throw\n");
    exit(1);
} catch (Throwable $e) {
    // expected
}

$mapped = nexrec_nexapp_map_role('org_admin');
if ($mapped !== 'admin') {
    fwrite(STDERR, "org_admin map\n");
    exit(1);
}
if (nexrec_nexapp_map_role('user') !== 'operator') {
    fwrite(STDERR, "default map operator\n");
    exit(1);
}

$gated = nexrec_nexapp_gate_instance(
    ['ok' => true, 'status' => 200, 'allowed_instances' => ['a', 'b']],
    'c'
);
if (!empty($gated['ok'])) {
    fwrite(STDERR, "gate should fail\n");
    exit(1);
}

echo "test_nexrec_nexapp.php ok\n";

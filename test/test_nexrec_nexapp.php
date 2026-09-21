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

putenv('NEXAPP_ISSUER=https://nexapp.nexstar.tv');
putenv('NEXAPP_SERVICE_ID=nexclip-recorder-ctl1');
$url = nexrec_nexapp_sso_url('/export');
if (!str_contains($url, '/launch.php?service_id=nexclip-recorder-ctl1')) {
    fwrite(STDERR, "sso url missing launch.php: {$url}\n");
    exit(1);
}
if (!str_contains($url, 'next=%2Fexport')) {
    fwrite(STDERR, "sso url missing next: {$url}\n");
    exit(1);
}
if (str_contains($url, 'instance_id')) {
    fwrite(STDERR, "sso url must not gate on instance_id: {$url}\n");
    exit(1);
}
if (nexrec_nexapp_looks_like_jwt('abc.def.ghi') !== true) {
    fwrite(STDERR, "jwt heuristic\n");
    exit(1);
}
if (nexrec_nexapp_looks_like_jwt('one-time-ticket') !== false) {
    fwrite(STDERR, "ticket heuristic\n");
    exit(1);
}

$tmp = sys_get_temp_dir() . '/nexrec-redeem-' . bin2hex(random_bytes(3)) . '.json';
file_put_contents($tmp, json_encode([
    'ok' => true,
    'sub' => 'u1',
    'email' => 'a@b',
    'name' => 'A',
    'role' => 'admin',
    'theme' => 'dark',
]));
putenv('NEXREC_NEXAPP_TICKET_STUB=' . $tmp);
$red = nexrec_nexapp_redeem_launch('ticket-1');
if (empty($red['ok']) || ($red['sub'] ?? '') !== 'u1' || ($red['theme'] ?? '') !== 'dark') {
    fwrite(STDERR, "redeem stub failed\n");
    exit(1);
}
unlink($tmp);

putenv('NEXAPP_PUBLIC_KEY_PATH=/no/such/nexapp-jwt.pem');
try {
    nexrec_nexapp_verify_jwt('aaa.bbb.ccc');
    fwrite(STDERR, "missing pem should hard-fail\n");
    exit(1);
} catch (Throwable $e) {
    if (!str_contains($e->getMessage(), 'unreadable')) {
        fwrite(STDERR, "missing pem message: " . $e->getMessage() . "\n");
        exit(1);
    }
}

echo "test_nexrec_nexapp.php ok\n";

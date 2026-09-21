#!/usr/bin/env php
<?php
/**
 * Ops allowlist: unit/verb patterns, and the sudoers helper rejects anything else.
 */
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-ops.php';

function fail(string $msg): never {
    fwrite(STDERR, $msg . "\n");
    exit(1);
}

$allowed = [
    'mediamtx.service',
    'nexrec-export.service',
    'nexrec-cleanup.service',
    'nexrec-cleanup.timer',
    'nexrec-analyze.service',
    'nexrec-nexclip.service',
    'nexrec-nexclip.timer',
    'nexrec-record@demo.service',
    'nexrec-record@studio-a.service',
    'nexrec-preview@in1.service',
];
foreach ($allowed as $u) {
    if (!nexrec_ops_unit_allowed($u)) {
        fail("should allow {$u}");
    }
}
$denied = [
    'apache2.service',
    'nexrec-record@Demo.service',
    'nexrec-record@.service',
    'mediamtx.service;id',
    "mediamtx.service\nrm",
    'nexrec-record@../../etc.service',
    'nexrec-analyze.timer',
    'nexrec-export.timer',
    'systemd-journald.service',
];
foreach ($denied as $u) {
    if (nexrec_ops_unit_allowed($u)) {
        fail("should deny {$u}");
    }
}
foreach (['start', 'stop', 'restart', 'enable', 'disable', 'show', 'journal'] as $v) {
    if (!nexrec_ops_verb_allowed($v)) {
        fail("verb {$v}");
    }
}
if (nexrec_ops_verb_allowed('status') || nexrec_ops_verb_allowed('isolate')) {
    fail('status/isolate must be rejected');
}

$names = nexrec_ops_unit_names(['cam-1', 'BAD', 'ok']);
if (!in_array('nexrec-record@cam-1.service', $names, true) || !in_array('nexrec-preview@ok.service', $names, true)) {
    fail('per-input units missing');
}
if (in_array('nexrec-record@BAD.service', $names, true)) {
    fail('bad input id became a unit');
}
if (!in_array('nexrec-analyze.service', $names, true)) {
    fail('analyze unit missing');
}

$parsed = nexrec_ops_parse_show("ActiveState=active\nUnitFileState=enabled\nSubState=running\nActiveEnterTimestamp=\n");
if ($parsed['active'] !== 'active' || $parsed['enabled'] !== 'enabled') {
    fail('show parse');
}
$unknown = nexrec_ops_parse_show('');
if ($unknown['active'] !== 'unknown') {
    fail('empty show should be unknown');
}

$root = dirname(__DIR__);
$helper = $root . '/bin/nexrec-systemctl.sh';
if (!is_file($helper)) {
    fail('helper missing');
}
$binDir = sys_get_temp_dir() . '/nexrec-sys-' . bin2hex(random_bytes(3));
mkdir($binDir, 0700, true);
$log = $binDir . '/log';
$fake = $binDir . '/systemctl';
file_put_contents($fake, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> " . escapeshellarg($log) . "\nexit 0\n");
$journal = $binDir . '/journalctl';
file_put_contents($journal, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> " . escapeshellarg($log) . "\necho line-one\nexit 0\n");
chmod($fake, 0755);
chmod($journal, 0755);
chmod($helper, 0755);

$env = [
    'NEXREC_SYSTEMCTL_BIN' => $fake,
    'NEXREC_JOURNALCTL_BIN' => $journal,
    'PATH' => '/usr/bin:/bin',
];
$run = static function (array $args) use ($helper, $env): array {
    $cmd = array_merge([$helper], $args);
    $desc = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $proc = proc_open($cmd, $desc, $pipes, null, $env, ['bypass_shell' => true]);
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $rc = proc_close($proc);
    return ['rc' => $rc, 'out' => (string) $out, 'err' => (string) $err];
};

@unlink($log);
$ok = $run(['restart', 'nexrec-record@studio-a.service']);
if ($ok['rc'] !== 0) {
    fail('restart allowed unit failed: ' . $ok['err']);
}
$logged = (string) file_get_contents($log);
if (!str_contains($logged, 'restart nexrec-record@studio-a.service')) {
    fail('fake systemctl did not see restart: ' . $logged);
}

$bad = $run(['start', 'apache2.service']);
if ($bad['rc'] !== 2 || !str_contains($bad['err'], 'unit not allowed')) {
    fail('apache2 should be rejected');
}
$inject = $run(['start', 'mediamtx.service;id']);
if ($inject['rc'] !== 2) {
    fail('injection unit should be rejected');
}
$verb = $run(['isolate', 'mediamtx.service']);
if ($verb['rc'] !== 2) {
    fail('isolate should be rejected');
}
$extra = $run(['start', 'mediamtx.service', '80']);
if ($extra['rc'] !== 2) {
    fail('extra arg on start should be rejected');
}
$big = $run(['journal', 'mediamtx.service', '9999']);
if ($big['rc'] !== 2) {
    fail('journal line cap');
}
@unlink($log);
$j = $run(['journal', 'nexrec-analyze.service', '80']);
if ($j['rc'] !== 0 || !str_contains($j['out'], 'line-one')) {
    fail('journal should pass -n 80 to journalctl');
}
$jlog = (string) file_get_contents($log);
if (!str_contains($jlog, '-n 80') || !str_contains($jlog, 'nexrec-analyze.service')) {
    fail('journal argv: ' . $jlog);
}

try {
    nexrec_ops_control('start', 'apache2.service');
    fail('php control should reject apache2 before exec');
} catch (InvalidArgumentException $e) {
    // expected
}

echo "test_nexrec_ops_allowlist.php ok\n";
exit(0);

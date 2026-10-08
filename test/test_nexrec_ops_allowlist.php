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
    'nexrec-deliver.service',
    'nexrec-cleanup.service',
    'nexrec-cleanup.timer',
    'nexrec-analyze.service',
    'nexrec-nexclip.service',
    'nexrec-nexclip.timer',
    'nexrec-decklink-configure.service',
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
foreach (['start', 'stop', 'restart', 'enable', 'disable', 'show', 'journal', 'journal-since'] as $v) {
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
try {
    nexrec_ops_wrapper('journal-since', 'mediamtx.service', 48);
    fail('journal-since must not use the short journal wrapper');
} catch (InvalidArgumentException $e) {
    // expected
}

if (nexrec_ops_unit_allowed('apache2.service') || nexrec_ops_unit_allowed('nexrec-metrics.service')) {
    fail('metrics and apache stay off the start/stop list');
}
if (!nexrec_ops_log_unit_allowed('apache2.service') || !nexrec_ops_log_unit_allowed('nexrec-metrics.service')) {
    fail('support pack should include metrics and apache');
}
if (!nexrec_ops_log_unit_allowed('nexrec-record@cam-1.service') || nexrec_ops_log_unit_allowed('sshd.service')) {
    fail('log unit allowlist');
}
$packUnits = nexrec_ops_log_unit_names(['cam-1']);
if (!in_array('nexrec-record@cam-1.service', $packUnits, true) || !in_array('apache2.service', $packUnits, true)) {
    fail('log unit list');
}
if (nexrec_ops_log_window_ok('12') || !nexrec_ops_log_window_ok('all') || !nexrec_ops_log_window_ok('72')) {
    fail('log window');
}
if (nexrec_ops_log_download_name('rec box', '48', '20261008T182700Z') !== 'nexrec-logs-recbox-20261008T182700Z-48h.zip') {
    fail('download name');
}
if (nexrec_ops_log_download_name('host', 'all', '20261008T182700Z') !== 'nexrec-logs-host-20261008T182700Z-all.zip') {
    fail('all download name');
}
$manifest = nexrec_ops_log_manifest('box', '48', '2026-10-08T18:27:00Z', [[
    'unit' => 'nexrec-export.service',
    'bytes' => 12,
    'truncated' => true,
    'rc' => 0,
    'err' => '',
]]);
if (!str_contains($manifest, 'window: 48 hours') || !str_contains($manifest, 'truncated=yes')) {
    fail('manifest');
}
putenv('NEXREC_OPS_NO_SUDO=1');
$since = nexrec_ops_journal_since_cmd('apache2.service', '48');
if ($since[0] === '/usr/bin/sudo' || array_slice($since, -3) !== ['journal-since', 'apache2.service', '48']) {
    fail('journal-since argv: ' . implode(' ', $since));
}
try {
    nexrec_ops_journal_since_cmd('sshd.service', '24');
    fail('sshd should be rejected');
} catch (InvalidArgumentException $e) {
    // expected
}

$capDir = sys_get_temp_dir() . '/nexrec-cap-' . bin2hex(random_bytes(3));
mkdir($capDir, 0700, true);
$capFile = $capDir . '/out.log';
$capped = nexrec_ops_capture_to_file([PHP_BINARY, '-r', 'echo str_repeat("ab", 40);'], $capFile, 10);
if (!$capped['truncated'] || $capped['bytes'] !== 10 || file_get_contents($capFile) !== 'ababababab') {
    fail('capture cap');
}
$logBody = $capDir . '/unit.log';
file_put_contents($logBody, "hello journal\n");
$manBody = $capDir . '/manifest.txt';
file_put_contents($manBody, $manifest);
$zipPath = $capDir . '/logs.zip';
nexrec_ops_zip_store($zipPath, [
    ['name' => 'journal/nexrec-export.service.log', 'path' => $logBody],
    ['name' => 'manifest.txt', 'path' => $manBody],
]);
$zipRaw = (string) file_get_contents($zipPath);
if (!str_starts_with($zipRaw, "PK\x03\x04") || !str_contains($zipRaw, 'journal/nexrec-export.service.log') || !str_contains($zipRaw, "hello journal\n")) {
    fail('zip store');
}
if (!str_contains($zipRaw, "PK\x05\x06")) {
    fail('zip missing end record');
}
nexrec_ops_rm_tree($capDir);

@unlink($log);
$span = $run(['journal-since', 'apache2.service', '48']);
if ($span['rc'] !== 0 || !str_contains($span['out'], 'line-one')) {
    fail('journal-since 48 failed: ' . $span['err']);
}
$spanLog = (string) file_get_contents($log);
if (!str_contains($spanLog, '--since 48 hours ago') || !str_contains($spanLog, '-u apache2.service')) {
    fail('journal-since argv: ' . $spanLog);
}
@unlink($log);
$allWin = $run(['journal-since', 'nexrec-metrics.service', 'all']);
if ($allWin['rc'] !== 0) {
    fail('journal-since all failed: ' . $allWin['err']);
}
$allLog = (string) file_get_contents($log);
if (str_contains($allLog, '--since') || !str_contains($allLog, 'nexrec-metrics.service')) {
    fail('all window should omit --since: ' . $allLog);
}
$badWin = $run(['journal-since', 'mediamtx.service', '12']);
if ($badWin['rc'] !== 2 || !str_contains($badWin['err'], 'bad window')) {
    fail('bad window should be rejected');
}
$startApache = $run(['start', 'nexrec-metrics.service']);
if ($startApache['rc'] !== 2 || !str_contains($startApache['err'], 'unit not allowed')) {
    fail('metrics must not be startable');
}
$injectWin = $run(['journal-since', 'mediamtx.service', '48;id']);
if ($injectWin['rc'] !== 2) {
    fail('window injection should be rejected');
}

echo "test_nexrec_ops_allowlist.php ok\n";
exit(0);

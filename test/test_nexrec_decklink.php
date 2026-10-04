#!/usr/bin/env php
<?php
/**
 * DeckLink validation, status JSON, and preview-unit policy. No hardware.
 */
declare(strict_types=1);

$root = dirname(__DIR__);
$tmp = sys_get_temp_dir() . '/nexrec-decklink-' . bin2hex(random_bytes(4));
mkdir($tmp, 0700, true);
putenv('NEXREC_DATA_DIR=' . $tmp);
putenv('NEXREC_DB=' . $tmp . '/nexrec.db');
putenv('NEXREC_DECKLINK_STATUS_BIN');

require $root . '/web/nexrec-ops.php';
require $root . '/web/nexrec-auth-lib.php';

function fail(string $msg): never {
    fwrite(STDERR, $msg . "\n");
    exit(1);
}

if (nexrec_decklink_input_error('decklink', 'DeckLink Quad 2 (1)', 'Hi59') !== '') {
    fail('quad name should be valid');
}
if (nexrec_decklink_input_error('decklink', '0', '') !== '') {
    fail('index should be valid');
}
if (nexrec_decklink_input_error('decklink', '', '') === '') {
    fail('empty device must fail');
}
if (nexrec_decklink_input_error('decklink', 'DeckLink; rm', '') === '') {
    fail('metacharacters must fail');
}
if (nexrec_decklink_input_error('decklink', 'DeckLink Quad 2 (1)', 'Hi 59') === '') {
    fail('format code with space must fail');
}
if (nexrec_decklink_input_error('rtsp', '', '') !== '') {
    fail('ip inputs do not require a decklink device');
}

$raw = (string) file_get_contents($root . '/test/fixtures/decklink-status.json');
$busy = nexrec_decklink_signal_from_json($raw, 'DeckLink Quad 2 (1)');
if (($busy['signal'] ?? '') !== 'present' || (int) ($busy['sdi_lock'] ?? 0) !== 1) {
    fail('busy connector should still report lock');
}
if (($busy['format'] ?? '') !== '1080i59.94' || (int) ($busy['busy'] ?? 0) !== 1) {
    fail('busy connector should still report format');
}
if (!str_contains((string) $busy['detail'], 'busy')) {
    fail('detail should mention busy');
}
$idle = nexrec_decklink_signal_from_json($raw, '1');
if (($idle['signal'] ?? '') !== 'no_signal' || ($idle['format'] ?? 'x') !== '') {
    fail('unlocked sub-device');
}
$missing = nexrec_decklink_signal_from_json($raw, 'DeckLink Quad 2 (8)');
if (($missing['probe'] ?? '') !== 'tool' || ($missing['signal'] ?? '') !== 'unknown') {
    fail('missing sub-device');
}
$drivers = nexrec_decklink_signal_from_json('{"devices":[],"error":"no_decklink_api"}', 'DeckLink Duo (1)');
if (($drivers['probe'] ?? '') !== 'unavailable' || !str_contains((string) $drivers['detail'], 'drivers')) {
    fail('no api message');
}

$list = (string) file_get_contents($root . '/test/fixtures/decklink-list-devices.txt');
$devs = nexrec_decklink_parse_devices($list);
if (($devs[0]['name'] ?? '') !== 'DeckLink Quad 2 (1)' || ($devs[1]['index'] ?? -1) !== 1) {
    fail('device list parse');
}
$fmts = nexrec_decklink_parse_formats("Supported formats for 'DeckLink Quad 2 (1)':\n\t'Hi59'\t1920x1080\n");
if ($fmts !== ['Hi59']) {
    fail('format list parse');
}
if (nexrec_decklink_ffmpeg_enabled("Unknown input format: 'decklink'\n")) {
    fail('stock ffmpeg should not look decklink-enabled');
}

$inputsPage = (string) file_get_contents($root . '/web/pages/inputs.html');
if (!str_contains($inputsPage, 'id="live_only"') || !str_contains($inputsPage, 'Live only')) {
    fail('inputs page needs a Live only checkbox');
}
if (!str_contains($inputsPage, 'id="input-dialog"') || !str_contains($inputsPage, 'Configure')) {
    fail('inputs page should edit from a modal');
}
if (!str_contains($inputsPage, 'id="intel-block"')) {
    fail('monitoring belongs in the input modal');
}
if (!str_contains($inputsPage, 'id="id" type="hidden"') || !str_contains($inputsPage, 'id="preview_path" type="hidden"')) {
    fail('id and preview path are generated, not typed');
}
if (preg_match('/<option value="testsrc">/', $inputsPage)) {
    fail('testsrc is not a station input type');
}
$exportPage = (string) file_get_contents($root . '/web/pages/export.html');
if (!str_contains($exportPage, 'live_only')) {
    fail('export page must leave Live only inputs out');
}

nexrec_migrate();
$db = nexrec_db();
$db->exec("INSERT INTO inputs (id,name,source_type,decklink_device,enabled,live_transcode,copy_native,upconvert_1080i,retention_days,preview_enabled,created_at,updated_at)
  VALUES ('sdi1','SDI 1','decklink','DeckLink Quad 2 (1)',1,1,0,0,28,1,'2026-09-22T00:00:00Z','2026-09-22T00:00:00Z')");
$db->exec("INSERT INTO inputs (id,name,source_type,url,enabled,live_transcode,copy_native,upconvert_1080i,retention_days,preview_enabled,created_at,updated_at)
  VALUES ('cam','Cam','rtsp','rtsp://example/stream',1,0,1,0,28,1,'2026-09-22T00:00:00Z','2026-09-22T00:00:00Z')");
$db->exec("INSERT INTO inputs (id,name,source_type,decklink_device,enabled,live_transcode,copy_native,upconvert_1080i,retention_days,preview_enabled,created_at,updated_at)
  VALUES ('sdi-live','SDI live','decklink','DeckLink Duo (1)',1,1,0,0,28,1,'2026-09-22T00:00:00Z','2026-09-22T00:00:00Z')");
$db->exec("UPDATE inputs SET live_only=1 WHERE id IN ('cam','sdi-live')");

if (!nexrec_ops_decklink_preview_unit('nexrec-preview@sdi1.service')) {
    fail('decklink preview unit must be blocked');
}
if (nexrec_ops_decklink_preview_unit('nexrec-preview@cam.service')) {
    fail('ip preview unit must be allowed');
}
if (nexrec_ops_decklink_preview_unit('nexrec-record@sdi1.service')) {
    fail('record unit is not a preview unit');
}
$blocked = nexrec_ops_control('start', 'nexrec-preview@sdi1.service');
if (!empty($blocked['ok']) || ($blocked['via'] ?? '') !== 'policy') {
    fail('start of decklink preview must be refused');
}
if (!str_contains((string) ($blocked['error'] ?? ''), 'exclusive-open')) {
    fail('refusal should explain the tee');
}

$units = nexrec_ops_units_payload();
$skipped = null;
foreach ($units as $u) {
    if (($u['unit'] ?? '') === 'nexrec-preview@sdi1.service') {
        $skipped = $u;
    }
}
if ($skipped === null || empty($skipped['skipped']) || ($skipped['active'] ?? '') !== 'skipped') {
    fail('services should mark decklink preview skipped');
}
if (!nexrec_ops_live_only_ip_record('nexrec-record@cam.service')) {
    fail('live only IP record unit must be blocked');
}
if (nexrec_ops_live_only_ip_record('nexrec-record@sdi1.service')) {
    fail('a recording DeckLink input is not live only');
}
if (nexrec_ops_live_only_ip_record('nexrec-record@sdi-live.service')) {
    fail('live only DeckLink still uses the record unit');
}
$refused = nexrec_ops_control('start', 'nexrec-record@cam.service');
if (!empty($refused['ok']) || ($refused['via'] ?? '') !== 'policy') {
    fail('start of a live only IP record unit must be refused');
}
$byUnit = [];
foreach ($units as $u) {
    $byUnit[(string) ($u['unit'] ?? '')] = $u;
}
$ipRec = $byUnit['nexrec-record@cam.service'] ?? null;
if ($ipRec === null || empty($ipRec['skipped'])) {
    fail('services should skip a live only IP record unit');
}
$ipPrev = $byUnit['nexrec-preview@cam.service'] ?? null;
if ($ipPrev === null || !empty($ipPrev['skipped'])) {
    fail('live only IP preview must stay available');
}
$sdiLive = $byUnit['nexrec-record@sdi-live.service'] ?? null;
if ($sdiLive === null || !empty($sdiLive['skipped']) || !str_contains((string) ($sdiLive['note'] ?? ''), 'does not write segments')) {
    fail('live only DeckLink record unit should say it does not write segments');
}

$probe = nexrec_ops_decklink_probe('DeckLink Quad 2 (1)');
if (($probe['probe'] ?? '') === 'tool') {
    fail('missing helper must not look like a live probe');
}
if (!str_contains((string) ($probe['detail'] ?? ''), 'needs DeckLink tools')) {
    fail('missing helper message, got ' . ($probe['detail'] ?? ''));
}

echo "test_nexrec_decklink.php ok\n";

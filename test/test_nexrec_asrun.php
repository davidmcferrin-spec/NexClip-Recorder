#!/usr/bin/env php
<?php
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-auth-lib.php';
require dirname(__DIR__) . '/web/nexrec-asrun.php';

$fixture = file_get_contents(dirname(__DIR__) . '/test/fixtures/asrun-sample.asr');
if (!is_string($fixture) || $fixture === '') {
    fwrite(STDERR, "missing as-run fixture\n");
    exit(1);
}

$parsed = nexrec_asrun_parse($fixture, 'America/New_York', '04:00:00');
if ($parsed['channel'] !== 'ANTV' || $parsed['broadcast_date'] !== '2026-09-28') {
    fwrite(STDERR, "channel/date mismatch\n");
    exit(1);
}

function find_house(array $events, string $house, string $machine): ?array {
    foreach ($events as $ev) {
        if ($ev['house_id'] === $house && $ev['machine'] === $machine) {
            return $ev;
        }
    }
    return null;
}

$open = find_house($parsed['events'], 'TER301-01', 'PLYX4');
if ($open === null || $open['start_at'] !== '2026-09-28T08:00:00.067Z') {
    fwrite(STDERR, "4am open did not land on 08:00:00.067Z, got " . ($open['start_at'] ?? 'missing') . "\n");
    exit(1);
}
if ($open['house_number'] !== 'TER301' || $open['segment'] !== '01' || $open['content_type'] !== 'ENT') {
    fwrite(STDERR, "house/segment split failed\n");
    exit(1);
}
if ($open['title'] !== "Three's Company" || $open['parent_id'] !== null) {
    fwrite(STDERR, "program title or base parent\n");
    exit(1);
}

$spot = find_house($parsed['events'], 'K501752', 'PLYX4');
if ($spot === null || $spot['content_type'] !== 'CM' || $spot['on_timeline'] !== 1 || $spot['parent_id'] !== null) {
    fwrite(STDERR, "commercial base missing\n");
    exit(1);
}
if (strpos((string) $spot['title'], 'LIFELOCK') === false) {
    fwrite(STDERR, "commercial title missing\n");
    exit(1);
}

$incoming = find_house($parsed['events'], 'TER304-01', 'PLYX4');
$credit = find_house($parsed['events'], 'TER301-05', 'PLXB4');
$cg = find_house($parsed['events'], 'NSML7100', 'ANCHY E');
if ($incoming === null || $credit === null || $cg === null) {
    fwrite(STDERR, "squeeze group missing\n");
    exit(1);
}
if ($credit['parent_id'] !== $incoming['id'] || $cg['parent_id'] !== $incoming['id']) {
    fwrite(STDERR, "squeeze rows are not tied to the incoming segment\n");
    exit(1);
}
if ($credit['segment'] !== '05' || $credit['content_type'] !== 'PG' || $credit['house_number'] !== 'TER301') {
    fwrite(STDERR, "outgoing credit fields\n");
    exit(1);
}
$later = find_house($parsed['events'], 'TER304-05', 'PLXB4');
$jeff = find_house($parsed['events'], 'JRR622-01', 'PLYX4');
if ($later === null || $jeff === null || $later['parent_id'] !== $jeff['id'] || $later['segment'] !== '05') {
    fwrite(STDERR, "second squeeze is not tied to Jeffersons segment 01\n");
    exit(1);
}

$late = find_house($parsed['events'], 'TTSR221-01', 'PLYX4');
if ($late === null || $late['start_at'] !== '2026-09-29T07:00:00.033Z') {
    fwrite(STDERR, "3am event did not roll to the next morning, got " . ($late['start_at'] ?? 'missing') . "\n");
    exit(1);
}

$notes = 0;
foreach ($parsed['events'] as $ev) {
    if ($ev['status'] === 'C' || $ev['status'] === 'P') {
        $notes++;
        if ($ev['on_timeline'] !== 0 || $ev['start_at'] !== null) {
            fwrite(STDERR, "comment landed on the timeline\n");
            exit(1);
        }
    }
}
if ($notes < 2) {
    fwrite(STDERR, "expected comment rows\n");
    exit(1);
}

$drop = sys_get_temp_dir() . '/nexrec-asrun-drop-' . bin2hex(random_bytes(4));
$channelDir = $drop . DIRECTORY_SEPARATOR . 'ANTVX';
mkdir($channelDir, 0700, true);
file_put_contents($channelDir . DIRECTORY_SEPARATOR . 'inputs', "# air\nantv\nbackup\n");
file_put_contents($channelDir . DIRECTORY_SEPARATOR . 'timezone', "America/New_York\n");
file_put_contents($channelDir . DIRECTORY_SEPARATOR . 'day-start', "04:00:00\n");
file_put_contents($channelDir . DIRECTORY_SEPARATOR . 'ANTVX_N260928.asr', $fixture);
file_put_contents($drop . DIRECTORY_SEPARATOR . 'loose.asr', $fixture);
$outside = sys_get_temp_dir() . '/nexrec-asrun-outside-' . bin2hex(random_bytes(3)) . '.asr';
file_put_contents($outside, $fixture);
if (DIRECTORY_SEPARATOR === '/') {
    symlink($outside, $channelDir . DIRECTORY_SEPARATOR . 'linked.asr');
}
$files = nexrec_asrun_drop_files($drop);
$bases = array_map('basename', $files);
if (!in_array('ANTVX_N260928.asr', $bases, true) || !in_array('loose.asr', $bases, true)) {
    fwrite(STDERR, "drop scan missed a log: " . implode(',', $bases) . "\n");
    exit(1);
}
if (in_array('linked.asr', $bases, true)) {
    fwrite(STDERR, "drop scan followed a symlink out of the folder\n");
    exit(1);
}
$cfg = nexrec_asrun_folder_config($channelDir);
if ($cfg['timezone'] !== 'America/New_York' || $cfg['day_start'] !== '04:00:00' || !$cfg['has_inputs']) {
    fwrite(STDERR, "folder config mismatch\n");
    exit(1);
}
if ($cfg['input_ids'] !== ['antv', 'backup']) {
    fwrite(STDERR, "folder inputs mismatch\n");
    exit(1);
}
if (nexrec_asrun_rel($drop, $channelDir . DIRECTORY_SEPARATOR . 'ANTVX_N260928.asr') !== 'ANTVX/ANTVX_N260928.asr') {
    fwrite(STDERR, "relative path mismatch\n");
    exit(1);
}

nexrec_migrate();
$saved = nexrec_asrun_import($fixture, [
    'filename' => 'ANTVX_N260928.asr',
    'timezone' => 'America/New_York',
    'day_start' => '04:00:00',
    'input_ids' => [],
]);
if ($saved['event_count'] < 10 || $saved['channel'] !== 'ANTV') {
    fwrite(STDERR, "import count\n");
    exit(1);
}
$again = nexrec_asrun_import($fixture, [
    'filename' => 'ANTVX_N260928.asr',
    'timezone' => 'America/New_York',
    'day_start' => '04:00:00',
]);
if (!$again['replaced'] || $again['id'] !== $saved['id']) {
    fwrite(STDERR, "reimport should replace the same channel day\n");
    exit(1);
}

$db = nexrec_db();
$db->exec("INSERT INTO inputs (id,name,source_type,enabled,live_transcode,copy_native,upconvert_1080i,retention_days,preview_enabled,created_at,updated_at)
  VALUES ('antv','Antenna','decklink',1,0,1,0,28,1,'2026-09-28T00:00:00Z','2026-09-28T00:00:00Z')
  ON CONFLICT (id) DO NOTHING");
nexrec_asrun_link($saved['id'], ['antv']);
$band = nexrec_asrun_band('antv', '2026-09-28T08:00:00Z', '2026-09-28T09:30:00Z');
$houses = array_column($band, 'house_id');
if (!in_array('TER301-01', $houses, true) || !in_array('K501752', $houses, true)) {
    fwrite(STDERR, "band missing program or commercial: " . implode(',', $houses) . "\n");
    exit(1);
}
if (in_array('TER304-05', $houses, true) || in_array('NSML7100', $houses, true)) {
    fwrite(STDERR, "band included a tied squeeze row\n");
    exit(1);
}

$loaded = nexrec_asrun_events($saved['id']);
if (count($loaded) !== $saved['event_count']) {
    fwrite(STDERR, "stored event count\n");
    exit(1);
}

nexrec_asrun_delete($saved['id']);
if (nexrec_asrun_find($saved['id']) !== null) {
    fwrite(STDERR, "delete left the as-run\n");
    exit(1);
}

echo "test_nexrec_asrun.php ok\n";

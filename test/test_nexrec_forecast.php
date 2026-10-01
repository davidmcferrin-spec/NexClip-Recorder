#!/usr/bin/env php
<?php
/**
 * Days-left math. No database.
 */
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-forecast.php';

function fail(string $msg): never {
    fwrite(STDERR, $msg . "\n");
    exit(1);
}

$quiet = nexrec_storage_forecast(
    ['free_bytes' => 800, 'total_bytes' => 1000],
    [[
        'id' => 'in1',
        'name' => 'Cam',
        'enabled' => true,
        'retention_days' => 28,
        'stored_bytes' => 0,
        'span_seconds' => 0,
        'video_bitrate' => '800',
        'audio_bitrate' => '0',
    ]],
    [
        'max_used_percent' => 90,
        'warn_points' => 5,
        'floor_bytes' => 50,
        'default_video' => '12M',
        'default_audio' => '192k',
    ]
);
if ($quiet['warn']) {
    fail('20% used should stay quiet');
}
if ($quiet['used_pct'] !== 20.0) {
    fail('used percent got ' . $quiet['used_pct']);
}
// usable is min(900-200=700, 800-50=750) = 700 bytes; rate is 800 bps = 100 B/s
if ((int) $quiet['usable_bytes'] !== 700) {
    fail('usable bytes got ' . $quiet['usable_bytes']);
}
if ($quiet['inputs'][0]['rate_source'] !== 'bitrate') {
    fail('empty input should use the target bitrate');
}

$day = 86400;
$measured = nexrec_storage_forecast(
    ['free_bytes' => 140, 'total_bytes' => 1000],
    [[
        'id' => 'in1',
        'name' => 'Cam',
        'enabled' => true,
        'retention_days' => 28,
        'stored_bytes' => $day,
        'span_seconds' => $day,
        'path' => '/var/lib/nexrec/storage/inputs/in1/native',
        'video_bitrate' => '12M',
        'audio_bitrate' => '192k',
    ]],
    [
        'max_used_percent' => 90,
        'warn_points' => 5,
        'floor_bytes' => 50,
        'default_video' => '12M',
        'default_audio' => '192k',
    ]
);
if (!$measured['warn'] || !$measured['cuts_retention']) {
    fail('86% full and a short runway should warn that retention will be cut');
}
if ($measured['inputs'][0]['rate_source'] !== 'measured') {
    fail('a day of chunks should use measured rate');
}
if (($quiet['inputs'][0]['path'] ?? 'x') !== '') {
    fail('path should stay empty when the caller did not set one');
}
if ($measured['inputs'][0]['path'] !== '/var/lib/nexrec/storage/inputs/in1/native') {
    fail('forecast should keep the recording folder');
}
if ($measured['inputs'][0]['stored_bytes'] !== $day) {
    fail('stored bytes were dropped');
}
if (abs($measured['inputs'][0]['days_on_disk'] - 1.0) > 0.01) {
    fail('days on disk got ' . $measured['inputs'][0]['days_on_disk']);
}
if (strpos($measured['message'], 'Cam (28 days)') === false) {
    fail('message should name the input retention: ' . $measured['message']);
}
if (strpos($measured['message'], '86.0%') === false) {
    fail('message should include used percent: ' . $measured['message']);
}

$covers = nexrec_storage_forecast(
    ['free_bytes' => 140, 'total_bytes' => 1000],
    [[
        'id' => 'in1',
        'name' => 'Cam',
        'enabled' => true,
        'retention_days' => 1,
        'stored_bytes' => 10,
        'span_seconds' => 86400 * 100,
        'video_bitrate' => '12M',
        'audio_bitrate' => '192k',
    ]],
    [
        'max_used_percent' => 90,
        'warn_points' => 5,
        'floor_bytes' => 0,
        'default_video' => '12M',
        'default_audio' => '192k',
    ]
);
if (!$covers['warn'] || $covers['cuts_retention']) {
    fail('near the cap with a long runway should warn without a retention cut');
}
if (strpos($covers['message'], 'still covers') === false) {
    fail('message should say retention still fits: ' . $covers['message']);
}

$floor = nexrec_storage_forecast(
    ['free_bytes' => 40, 'total_bytes' => 200],
    [],
    [
        'max_used_percent' => 90,
        'warn_points' => 5,
        'floor_bytes' => 50,
        'default_video' => '12M',
        'default_audio' => '192k',
    ]
);
if (!$floor['warn'] || strpos($floor['message'], 'floor') === false) {
    fail('free space at the floor should warn: ' . $floor['message']);
}

if (nexrec_parse_size_bytes('50G') !== 50 * 1024 * 1024 * 1024) {
    fail('50G parse');
}
if ((int) nexrec_parse_bitrate_bps('12M') !== 12000000) {
    fail('12M bitrate');
}

echo "ok\n";

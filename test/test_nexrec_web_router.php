#!/usr/bin/env php
<?php
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-web-router.php';

$pages = nexrec_web_pages();
foreach (['/live', '/export', '/transfers', '/asruns', '/inputs', '/settings', '/users', '/metrics', '/services', '/login'] as $p) {
    if (!isset($pages[$p])) {
        fwrite(STDERR, "missing page {$p}\n");
        exit(1);
    }
}
$apis = nexrec_web_apis();
foreach (['/api/auth', '/api/recorder', '/api/nexclip', '/api/version'] as $p) {
    if (!isset($apis[$p])) {
        fwrite(STDERR, "missing api {$p}\n");
        exit(1);
    }
}
if (nexrec_app_root() === '') {
    fwrite(STDERR, "app root empty\n");
    exit(1);
}
$token = str_repeat('ab', 16);
if (nexrec_web_share_token('/s/' . $token) !== $token) {
    fwrite(STDERR, "share page token\n");
    exit(1);
}
if (nexrec_web_share_token('/s/short') !== null || nexrec_web_share_token('/export') !== null) {
    fwrite(STDERR, "share page rejected a bad token\n");
    exit(1);
}
$file = nexrec_web_share_file('/api/share/' . $token . '/cam-1');
if ($file === null || $file['token'] !== $token || $file['input_id'] !== 'cam-1') {
    fwrite(STDERR, "share file route\n");
    exit(1);
}
$under = nexrec_web_share_file('/api/share/' . $token . '/cam_1');
if ($under === null || $under['input_id'] !== 'cam_1') {
    fwrite(STDERR, "share file route rejected an underscore\n");
    exit(1);
}
$longId = 'a' . str_repeat('b', 63);
$long = nexrec_web_share_file('/api/share/' . $token . '/' . $longId);
if ($long === null || $long['input_id'] !== $longId) {
    fwrite(STDERR, "share file route rejected a 64-character id\n");
    exit(1);
}
if (nexrec_web_share_file('/api/share/' . $token . '/' . $longId . 'c') !== null) {
    fwrite(STDERR, "share file route accepted an id past 64 characters\n");
    exit(1);
}
if (nexrec_web_share_file('/api/share/' . $token . '/cam/1') !== null
    || nexrec_web_share_file('/api/share/' . $token . '/..') !== null) {
    fwrite(STDERR, "share file route accepted a path segment\n");
    exit(1);
}
if (nexrec_web_share_file('/api/exports/' . $token . '/file') !== null) {
    fwrite(STDERR, "export file route collided with share\n");
    exit(1);
}
echo "test_nexrec_web_router.php ok\n";

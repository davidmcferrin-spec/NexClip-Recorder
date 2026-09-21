#!/usr/bin/env php
<?php
declare(strict_types=1);

require dirname(__DIR__) . '/web/nexrec-web-router.php';

$pages = nexrec_web_pages();
foreach (['/live', '/export', '/inputs', '/settings', '/users', '/services', '/login'] as $p) {
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
echo "test_nexrec_web_router.php ok\n";

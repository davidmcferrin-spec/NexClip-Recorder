#!/usr/bin/env php
<?php
/**
 * Create SQLite DB + seed admin. Safe to re-run.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';

try {
    nexrec_load_station_env();
    nexrec_migrate();
    nexrec_seed_admin();
    $n = (int) nexrec_db()->querySingle('SELECT COUNT(*) FROM users');
    fwrite(STDOUT, "auth bootstrap ok: users={$n} db=" . nexrec_db_path() . "\n");
    exit(0);
} catch (Throwable $e) {
    fwrite(STDERR, 'auth bootstrap failed: ' . $e->getMessage() . "\n");
    exit(1);
}

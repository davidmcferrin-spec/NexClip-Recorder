#!/usr/bin/env php
<?php
/**
 * Hourly as-run drop import. cron: /etc/cron.d/nexrec (installed by setup.sh).
 *
 * NEXREC_ASRUN_DIR (default $NEXREC_DATA_DIR/asruns) holds one folder per
 * channel. Each folder may contain:
 *   *.asr       one broadcast-day log
 *   inputs      recorder input ids, one per line (ties the folder to those inputs)
 *   timezone    IANA zone, default America/New_York
 *   day-start   HH:MM:SS, default 04:00:00
 *
 * A file is imported when it is new or its mtime is newer than the stored log.
 * Without an inputs file, input links already set in the UI are kept.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "cli only\n");
    exit(1);
}

$envFile = null;
for ($i = 1; $i < $argc; $i++) {
    if ($argv[$i] === '--env' && isset($argv[$i + 1])) {
        $envFile = $argv[++$i];
    }
}
if (is_string($envFile) && $envFile !== '') {
    putenv('NEXREC_ENV_FILE=' . $envFile);
}

require dirname(__DIR__) . '/web/nexrec-auth-lib.php';
require dirname(__DIR__) . '/web/nexrec-asrun.php';

nexrec_load_station_env();
nexrec_migrate();

$result = nexrec_asrun_import_tree();
foreach ($result['errors'] as $err) {
    fwrite(STDERR, $err . "\n");
}
if ($result['imported'] > 0 || $result['errors'] !== []) {
    echo "as-run import: {$result['imported']} imported, {$result['skipped']} unchanged\n";
}
exit($result['errors'] === [] ? 0 : 1);

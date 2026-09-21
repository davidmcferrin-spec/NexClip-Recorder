#!/usr/bin/env php
<?php
declare(strict_types=1);

$tmp = sys_get_temp_dir() . '/nexrec-feat-' . bin2hex(random_bytes(4));
mkdir($tmp, 0700, true);
putenv('NEXREC_DATA_DIR=' . $tmp);
putenv('NEXREC_DB=' . $tmp . '/nexrec.db');
putenv('NEXREC_AUTH_HTTP=0');

require dirname(__DIR__) . '/web/nexrec-auth-lib.php';
nexrec_migrate();

$db = nexrec_db();
$res = $db->query('PRAGMA table_info(inputs)');
$cols = [];
while ($res && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
    $cols[] = $row['name'];
}
foreach (['feat_scte', 'feat_captions', 'thresh_black_s', 'transcribe_engine'] as $c) {
    if (!in_array($c, $cols, true)) {
        fwrite(STDERR, "missing inputs column {$c}\n");
        exit(1);
    }
}
foreach (['events', 'captions', 'loudness_samples', 'analyze_jobs'] as $t) {
    $n = $db->querySingle("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='{$t}'");
    if ((int) $n !== 1) {
        fwrite(STDERR, "missing table {$t}\n");
        exit(1);
    }
}

$db->exec("INSERT INTO inputs (id,name,source_type,enabled,live_transcode,copy_native,upconvert_1080i,retention_days,preview_enabled,created_at,updated_at)
  VALUES ('cam','cam','rtsp',1,0,1,0,28,1,'2026-09-21T00:00:00Z','2026-09-21T00:00:00Z')");
$db->exec("INSERT INTO captions (id,input_id,kind,service,speaker,t_start,t_end,pts,timecode,text,created_at)
  VALUES ('cap_1','cam','caption','608/708','','2026-09-21T15:00:01Z',NULL,1.0,'15:00:01:00','weather alert downtown','2026-09-21T15:00:01Z')");
$db->exec("INSERT INTO captions_fts (id,input_id,kind,t_start,speaker,text)
  VALUES ('cap_1','cam','caption','2026-09-21T15:00:01Z','','weather alert downtown')");

$st = $db->prepare('SELECT text FROM captions_fts WHERE captions_fts MATCH :q LIMIT 5');
$st->bindValue(':q', 'weather', SQLITE3_TEXT);
$row = $st->execute()->fetchArray(SQLITE3_ASSOC);
if (!$row || strpos((string) $row['text'], 'weather') === false) {
    fwrite(STDERR, "fts search missed\n");
    exit(1);
}

echo "test_nexrec_features.php ok\n";

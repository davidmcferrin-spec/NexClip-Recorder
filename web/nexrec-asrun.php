<?php
/**
 * Automation as-run (.asr) ingest.
 *
 * Fixed 510-byte records. Columns are byte offsets:
 *   0 station, 6 air date, 14 event number, 22 status,
 *   24 scheduled, 32 actual, 40 duration (each HHMMSSFF),
 *   71 machine, 90 relation (R tied, D cue), 102 class (P/B/O),
 *   121 HouseID, 262/282/302 titles, 334 content type.
 *
 * A broadcast day starts at day_start (default 04:00:00) in the as-run
 * zone (default America/New_York). A clock earlier than that start belongs
 * to the next calendar morning. Frames are a fraction of the wall-clock
 * second at 30 fps.
 */
declare(strict_types=1);

function nexrec_asrun_tc_parts(string $tc): ?array {
    if (!preg_match('/^(\d{2})(\d{2})(\d{2})(\d{2})$/', $tc, $m)) {
        return null;
    }
    return [(int) $m[1], (int) $m[2], (int) $m[3], (int) $m[4]];
}

function nexrec_asrun_tc_clock(string $tc): string {
    $p = nexrec_asrun_tc_parts($tc);
    if ($p === null) {
        return '';
    }
    return sprintf('%02d:%02d:%02d:%02d', $p[0], $p[1], $p[2], $p[3]);
}

function nexrec_asrun_tc_seconds(string $tc): float {
    $p = nexrec_asrun_tc_parts($tc);
    if ($p === null) {
        return 0.0;
    }
    return ($p[0] * 3600) + ($p[1] * 60) + $p[2] + ($p[3] / 30.0);
}

function nexrec_asrun_field(string $line, int $start, int $end): string {
    if (strlen($line) < $end) {
        return '';
    }
    return rtrim(substr($line, $start, $end - $start));
}

/**
 * @return array{house:string,segment:string}
 */
function nexrec_asrun_split_house(string $houseId): array {
    if (preg_match('/^(.+)-(\d+)$/', $houseId, $m)) {
        return ['house' => $m[1], 'segment' => $m[2]];
    }
    return ['house' => $houseId, 'segment' => ''];
}

function nexrec_asrun_display_title(
    string $content,
    string $t1,
    string $t2,
    string $t3,
    string $house,
    string $comment
): string {
    if ($comment !== '') {
        return $comment;
    }
    $t1 = rtrim($t1, " -");
    $t2 = trim($t2);
    if ($content === 'ENT') {
        if ($t2 !== '') {
            return $t2;
        }
        return $t1 !== '' ? $t1 : $house;
    }
    if ($content === 'CM' || $content === 'PRO' || $content === 'PR') {
        if ($t1 !== '' && $t2 !== '') {
            return $t1 . ' — ' . $t2;
        }
        if ($t1 !== '') {
            return $t1;
        }
        return $t2 !== '' ? $t2 : $house;
    }
    if ($t1 !== '' && $t2 !== '') {
        return $t1 . ' ' . $t2;
    }
    if ($t2 !== '') {
        return $t2;
    }
    if ($t1 !== '') {
        return $t1;
    }
    if ($t3 !== '') {
        return $t3;
    }
    return $house;
}

/**
 * Actual air time as a UTC timestamp. Null when the record has no air time.
 */
function nexrec_asrun_wall_utc(string $airDate, string $tc, string $tz, string $dayStart): ?string {
    if ($tc === '00000000' || nexrec_asrun_tc_parts($tc) === null) {
        return null;
    }
    $p = nexrec_asrun_tc_parts($tc);
    $clock = sprintf('%02d:%02d:%02d', $p[0], $p[1], $p[2]);
    $day = $airDate;
    if ($clock < $dayStart) {
        $next = DateTimeImmutable::createFromFormat('!Y-m-d', $airDate);
        if ($next === false) {
            return null;
        }
        $day = $next->modify('+1 day')->format('Y-m-d');
    }
    $local = DateTimeImmutable::createFromFormat('!Y-m-d H:i:s', $day . ' ' . $clock, new DateTimeZone($tz));
    if ($local === false) {
        return null;
    }
    $ms = (int) round($p[3] * 1000 / 30);
    if ($ms > 0) {
        $local = $local->modify('+' . $ms . ' milliseconds');
    }
    return $local->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d\TH:i:s.v\Z');
}

function nexrec_asrun_add_seconds(string $isoUtc, float $seconds): string {
    $dt = new DateTimeImmutable($isoUtc);
    $ms = (int) round($seconds * 1000);
    if ($ms !== 0) {
        $dt = $dt->modify(($ms > 0 ? '+' : '') . $ms . ' milliseconds');
    }
    return $dt->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d\TH:i:s.v\Z');
}

/**
 * @return array{channel:string,broadcast_date:string,events:list<array<string,mixed>>}
 */
function nexrec_asrun_parse(string $text, string $tz, string $dayStart): array {
    if (!in_array($tz, timezone_identifiers_list(), true)) {
        throw new InvalidArgumentException('unknown timezone');
    }
    if (!preg_match('/^\d{2}:\d{2}:\d{2}$/', $dayStart)) {
        throw new InvalidArgumentException('day start must be HH:MM:SS');
    }
    if (str_starts_with($text, "\xEF\xBB\xBF")) {
        $text = substr($text, 3);
    }
    $text = str_replace("\r\n", "\n", $text);
    $text = str_replace("\r", "\n", $text);
    $channel = '';
    $airDate = '';
    $events = [];
    $parentId = null;
    $seq = 0;
    foreach (explode("\n", $text) as $raw) {
        $line = rtrim($raw, "\r");
        if ($line === '' || strlen($line) < 146) {
            continue;
        }
        $station = trim(substr($line, 0, 6));
        $dateRaw = substr($line, 6, 8);
        if (!preg_match('/^\d{8}$/', $dateRaw)) {
            continue;
        }
        if ($channel === '' && $station !== '') {
            $channel = $station;
        }
        if ($airDate === '') {
            $airDate = substr($dateRaw, 0, 4) . '-' . substr($dateRaw, 4, 2) . '-' . substr($dateRaw, 6, 2);
        }
        $status = $line[22] === ' ' ? '' : $line[22];
        $relationCh = strlen($line) > 90 ? $line[90] : ' ';
        $relation = ($relationCh === 'R' || $relationCh === 'D') ? $relationCh : '';
        $classCh = strlen($line) > 102 ? $line[102] : ' ';
        $eventClass = ($classCh === 'P' || $classCh === 'B' || $classCh === 'O') ? $classCh : '';
        $actual = strlen($line) >= 40 ? substr($line, 32, 8) : '00000000';
        $duration = strlen($line) >= 48 ? substr($line, 40, 8) : '00000000';
        $machine = nexrec_asrun_field($line, 71, 90);
        $houseId = nexrec_asrun_field($line, 121, 146);
        $split = nexrec_asrun_split_house($houseId);
        $t1 = nexrec_asrun_field($line, 262, 282);
        $t2 = nexrec_asrun_field($line, 282, 302);
        $t3 = nexrec_asrun_field($line, 302, 334);
        $content = nexrec_asrun_field($line, 334, 354);
        $comment = '';
        if ($status === 'C' || $status === 'P') {
            $comment = trim(nexrec_asrun_field($line, 262, 354));
            $t1 = '';
            $t2 = '';
            $t3 = '';
            $content = '';
        }
        $title = nexrec_asrun_display_title($content, $t1, $t2, $t3, $houseId, $comment);
        $start = null;
        $end = null;
        $onTimeline = 0;
        if ($status === '' && $actual !== '00000000' && nexrec_asrun_tc_seconds($duration) > 0) {
            $start = nexrec_asrun_wall_utc($airDate, $actual, $tz, $dayStart);
            if ($start !== null) {
                $end = nexrec_asrun_add_seconds($start, nexrec_asrun_tc_seconds($duration));
                $onTimeline = 1;
            }
        }
        $isBase = $onTimeline === 1 && $relation === '' && ($eventClass === 'P' || $eventClass === 'B');
        $id = nexrec_new_id('ase');
        $parent = null;
        if ($isBase) {
            $parentId = $id;
        } elseif ($relation !== '' || $status === 'M') {
            $parent = $parentId;
        }
        $events[] = [
            'id' => $id,
            'seq' => $seq,
            'event_number' => trim(substr($line, 14, 8)),
            'status' => $status,
            'relation' => $relation,
            'event_class' => $eventClass,
            'parent_id' => $parent,
            'machine' => $machine,
            'house_id' => $houseId,
            'house_number' => $split['house'],
            'segment' => $split['segment'],
            'content_type' => $content,
            'title' => $title,
            'actual' => nexrec_asrun_tc_clock($actual),
            'duration_tc' => nexrec_asrun_tc_clock($duration),
            'start_at' => $start,
            'end_at' => $end,
            'on_timeline' => $onTimeline,
        ];
        $seq++;
    }
    if ($events === [] || $airDate === '') {
        throw new InvalidArgumentException('no as-run records in that file');
    }
    return [
        'channel' => $channel,
        'broadcast_date' => $airDate,
        'events' => $events,
    ];
}

/**
 * @param array<string,mixed> $opts text, filename, name, timezone, day_start, input_ids
 * @return array{id:string,event_count:int,replaced:bool,channel:string,broadcast_date:string}
 */
function nexrec_asrun_import(string $text, array $opts = []): array {
    $tz = trim((string) ($opts['timezone'] ?? 'America/New_York'));
    if ($tz === '') {
        $tz = 'America/New_York';
    }
    $dayStart = trim((string) ($opts['day_start'] ?? '04:00:00'));
    if ($dayStart === '') {
        $dayStart = '04:00:00';
    }
    $parsed = nexrec_asrun_parse($text, $tz, $dayStart);
    $filename = trim((string) ($opts['filename'] ?? ''));
    $name = trim((string) ($opts['name'] ?? ''));
    if ($name === '') {
        $name = trim($parsed['channel'] . ' ' . $parsed['broadcast_date']);
        if ($name === '') {
            $name = $filename !== '' ? $filename : 'As-run';
        }
    }
    $inputIds = $opts['input_ids'] ?? null;
    $db = nexrec_db();
    $existing = null;
    if ($parsed['channel'] !== '') {
        $st = $db->prepare('SELECT id FROM asruns WHERE channel = :c AND broadcast_date = :d');
        $st->bindValue(':c', $parsed['channel'], SQLITE3_TEXT);
        $st->bindValue(':d', $parsed['broadcast_date'], SQLITE3_TEXT);
        $existing = nexrec_row($st->execute());
    }
    $now = nexrec_now_iso();
    $replaced = $existing !== null;
    $id = $replaced ? (string) $existing['id'] : nexrec_new_id('asr');
    $db->exec('BEGIN');
    try {
        if ($replaced) {
            $del = $db->prepare('DELETE FROM asrun_events WHERE asrun_id = :id');
            $del->bindValue(':id', $id, SQLITE3_TEXT);
            $del->execute();
            $up = $db->prepare(
                'UPDATE asruns SET name=:n, filename=:f, timezone=:tz, day_start=:ds, event_count=:ec, updated_at=:u WHERE id=:id'
            );
            $up->bindValue(':n', $name, SQLITE3_TEXT);
            $up->bindValue(':f', $filename, SQLITE3_TEXT);
            $up->bindValue(':tz', $tz, SQLITE3_TEXT);
            $up->bindValue(':ds', $dayStart, SQLITE3_TEXT);
            $up->bindValue(':ec', count($parsed['events']), SQLITE3_INTEGER);
            $up->bindValue(':u', $now, SQLITE3_TEXT);
            $up->bindValue(':id', $id, SQLITE3_TEXT);
            $up->execute();
        } else {
            $ins = $db->prepare(
                'INSERT INTO asruns (id,name,filename,channel,broadcast_date,timezone,day_start,event_count,created_at,updated_at)
                 VALUES (:id,:n,:f,:c,:d,:tz,:ds,:ec,:created,:u)'
            );
            $ins->bindValue(':id', $id, SQLITE3_TEXT);
            $ins->bindValue(':n', $name, SQLITE3_TEXT);
            $ins->bindValue(':f', $filename, SQLITE3_TEXT);
            $ins->bindValue(':c', $parsed['channel'], SQLITE3_TEXT);
            $ins->bindValue(':d', $parsed['broadcast_date'], SQLITE3_TEXT);
            $ins->bindValue(':tz', $tz, SQLITE3_TEXT);
            $ins->bindValue(':ds', $dayStart, SQLITE3_TEXT);
            $ins->bindValue(':ec', count($parsed['events']), SQLITE3_INTEGER);
            $ins->bindValue(':created', $now, SQLITE3_TEXT);
            $ins->bindValue(':u', $now, SQLITE3_TEXT);
            $ins->execute();
        }
        $ev = $db->prepare(
            'INSERT INTO asrun_events (
                id,asrun_id,seq,event_number,status,relation,event_class,parent_id,
                machine,house_id,house_number,segment,content_type,title,actual,duration_tc,
                start_at,end_at,on_timeline
             ) VALUES (
                :id,:asrun,:seq,:num,:status,:rel,:class,:parent,
                :machine,:house,:hnum,:seg,:content,:title,:actual,:dur,
                :start,:end,:on
             )'
        );
        foreach ($parsed['events'] as $row) {
            $ev->bindValue(':id', $row['id'], SQLITE3_TEXT);
            $ev->bindValue(':asrun', $id, SQLITE3_TEXT);
            $ev->bindValue(':seq', $row['seq'], SQLITE3_INTEGER);
            $ev->bindValue(':num', $row['event_number'], SQLITE3_TEXT);
            $ev->bindValue(':status', $row['status'], SQLITE3_TEXT);
            $ev->bindValue(':rel', $row['relation'], SQLITE3_TEXT);
            $ev->bindValue(':class', $row['event_class'], SQLITE3_TEXT);
            $ev->bindValue(':parent', $row['parent_id'], $row['parent_id'] === null ? SQLITE3_NULL : SQLITE3_TEXT);
            $ev->bindValue(':machine', $row['machine'], SQLITE3_TEXT);
            $ev->bindValue(':house', $row['house_id'], SQLITE3_TEXT);
            $ev->bindValue(':hnum', $row['house_number'], SQLITE3_TEXT);
            $ev->bindValue(':seg', $row['segment'], SQLITE3_TEXT);
            $ev->bindValue(':content', $row['content_type'], SQLITE3_TEXT);
            $ev->bindValue(':title', $row['title'], SQLITE3_TEXT);
            $ev->bindValue(':actual', $row['actual'], SQLITE3_TEXT);
            $ev->bindValue(':dur', $row['duration_tc'], SQLITE3_TEXT);
            $ev->bindValue(':start', $row['start_at'], $row['start_at'] === null ? SQLITE3_NULL : SQLITE3_TEXT);
            $ev->bindValue(':end', $row['end_at'], $row['end_at'] === null ? SQLITE3_NULL : SQLITE3_TEXT);
            $ev->bindValue(':on', $row['on_timeline'], SQLITE3_INTEGER);
            $ev->execute();
        }
        if (is_array($inputIds)) {
            nexrec_asrun_link($id, $inputIds);
        }
        $db->exec('COMMIT');
    } catch (Throwable $e) {
        try {
            $db->exec('ROLLBACK');
        } catch (Throwable $ignored) {
        }
        throw $e;
    }
    return [
        'id' => $id,
        'event_count' => count($parsed['events']),
        'replaced' => $replaced,
        'channel' => $parsed['channel'],
        'broadcast_date' => $parsed['broadcast_date'],
    ];
}

/**
 * @param list<string> $inputIds
 */
function nexrec_asrun_link(string $asrunId, array $inputIds): void {
    $clean = [];
    foreach ($inputIds as $raw) {
        $id = trim((string) $raw);
        if ($id === '' || isset($clean[$id])) {
            continue;
        }
        if (!preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $id)) {
            throw new InvalidArgumentException('invalid input id');
        }
        $clean[$id] = true;
    }
    $db = nexrec_db();
    foreach (array_keys($clean) as $inputId) {
        $st = $db->prepare('SELECT id FROM inputs WHERE id = :i');
        $st->bindValue(':i', $inputId, SQLITE3_TEXT);
        if (nexrec_row($st->execute()) === null) {
            throw new InvalidArgumentException('unknown input');
        }
    }
    $del = $db->prepare('DELETE FROM asrun_inputs WHERE asrun_id = :a');
    $del->bindValue(':a', $asrunId, SQLITE3_TEXT);
    $del->execute();
    $ins = $db->prepare('INSERT INTO asrun_inputs (asrun_id, input_id) VALUES (:a, :i)');
    foreach (array_keys($clean) as $inputId) {
        $ins->bindValue(':a', $asrunId, SQLITE3_TEXT);
        $ins->bindValue(':i', $inputId, SQLITE3_TEXT);
        $ins->execute();
    }
}

function nexrec_asrun_find(string $id): ?array {
    $st = nexrec_db()->prepare('SELECT * FROM asruns WHERE id = :id');
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    return nexrec_row($st->execute());
}

/**
 * @return list<string>
 */
function nexrec_asrun_input_ids(string $asrunId): array {
    $st = nexrec_db()->prepare('SELECT input_id FROM asrun_inputs WHERE asrun_id = :a ORDER BY input_id');
    $st->bindValue(':a', $asrunId, SQLITE3_TEXT);
    $res = $st->execute();
    $out = [];
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $out[] = (string) $row['input_id'];
    }
    return $out;
}

/**
 * @return list<array<string,mixed>>
 */
function nexrec_asrun_events(string $asrunId): array {
    $st = nexrec_db()->prepare('SELECT * FROM asrun_events WHERE asrun_id = :a ORDER BY seq');
    $st->bindValue(':a', $asrunId, SQLITE3_TEXT);
    $res = $st->execute();
    $out = [];
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $out[] = nexrec_asrun_public_event($row);
    }
    return $out;
}

/**
 * @param array<string,mixed> $row
 * @return array<string,mixed>
 */
function nexrec_asrun_public_event(array $row): array {
    return [
        'id' => $row['id'],
        'seq' => (int) $row['seq'],
        'event_number' => $row['event_number'],
        'status' => $row['status'],
        'relation' => $row['relation'],
        'event_class' => $row['event_class'],
        'parent_id' => $row['parent_id'],
        'machine' => $row['machine'],
        'house_id' => $row['house_id'],
        'house_number' => $row['house_number'],
        'segment' => $row['segment'],
        'content_type' => $row['content_type'],
        'title' => $row['title'],
        'actual' => $row['actual'],
        'duration_tc' => $row['duration_tc'],
        'start_at' => $row['start_at'],
        'end_at' => $row['end_at'],
        'on_timeline' => (int) $row['on_timeline'],
    ];
}

/**
 * @return list<array<string,mixed>>
 */
function nexrec_asrun_list(): array {
    $db = nexrec_db();
    $res = $db->query('SELECT * FROM asruns ORDER BY broadcast_date DESC, channel');
    $rows = [];
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $rows[] = $row;
    }
    $links = [];
    $linkRes = $db->query(
        'SELECT l.asrun_id, l.input_id, i.name AS input_name
         FROM asrun_inputs l LEFT JOIN inputs i ON i.id = l.input_id
         ORDER BY i.name'
    );
    while ($link = $linkRes->fetchArray(SQLITE3_ASSOC)) {
        $links[(string) $link['asrun_id']][] = [
            'id' => $link['input_id'],
            'name' => $link['input_name'] ?: $link['input_id'],
        ];
    }
    $out = [];
    foreach ($rows as $row) {
        $out[] = [
            'id' => $row['id'],
            'name' => $row['name'],
            'filename' => $row['filename'],
            'channel' => $row['channel'],
            'broadcast_date' => $row['broadcast_date'],
            'timezone' => $row['timezone'],
            'day_start' => $row['day_start'],
            'event_count' => (int) $row['event_count'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
            'inputs' => $links[(string) $row['id']] ?? [],
        ];
    }
    return $out;
}

/**
 * Base program and commercial events for one input in a wall-clock window.
 *
 * @return list<array<string,mixed>>
 */
function nexrec_asrun_band(string $inputId, string $from, string $to): array {
    $st = nexrec_db()->prepare(
        'SELECT e.id, e.start_at, e.end_at, e.content_type, e.title, e.house_id, e.machine
         FROM asrun_events e
         JOIN asrun_inputs l ON l.asrun_id = e.asrun_id
         WHERE l.input_id = :i
           AND e.on_timeline = 1
           AND e.parent_id IS NULL
           AND e.content_type IN (\'ENT\', \'CM\', \'PRO\', \'PR\')
           AND e.start_at < :t_to
           AND e.end_at > :t_from
         ORDER BY e.start_at'
    );
    $st->bindValue(':i', $inputId, SQLITE3_TEXT);
    $st->bindValue(':t_to', $to, SQLITE3_TEXT);
    $st->bindValue(':t_from', $from, SQLITE3_TEXT);
    $res = $st->execute();
    $out = [];
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $out[] = [
            'id' => $row['id'],
            'start_at' => $row['start_at'],
            'end_at' => $row['end_at'],
            'content_type' => $row['content_type'],
            'title' => $row['title'],
            'house_id' => $row['house_id'],
            'machine' => $row['machine'],
        ];
    }
    return $out;
}

function nexrec_asrun_delete(string $id): void {
    $db = nexrec_db();
    $a = $db->prepare('DELETE FROM asrun_events WHERE asrun_id = :id');
    $a->bindValue(':id', $id, SQLITE3_TEXT);
    $a->execute();
    $b = $db->prepare('DELETE FROM asrun_inputs WHERE asrun_id = :id');
    $b->bindValue(':id', $id, SQLITE3_TEXT);
    $b->execute();
    $c = $db->prepare('DELETE FROM asruns WHERE id = :id');
    $c->bindValue(':id', $id, SQLITE3_TEXT);
    $c->execute();
}

function nexrec_asrun_drop_dir(): string {
    $configured = nexrec_env_get('NEXREC_ASRUN_DIR');
    if ($configured !== '') {
        return $configured;
    }
    return rtrim(nexrec_data_dir(), '/\\') . '/asruns';
}

function nexrec_asrun_rel(string $root, string $path): string {
    $root = rtrim(str_replace('\\', '/', $root), '/');
    $path = str_replace('\\', '/', $path);
    $prefix = $root . '/';
    if (str_starts_with($path, $prefix)) {
        return substr($path, strlen($prefix));
    }
    return basename($path);
}

function nexrec_asrun_inside(string $rootReal, string $path): bool {
    $real = realpath($path);
    if ($real === false || $rootReal === '') {
        return false;
    }
    $root = rtrim($rootReal, '/\\');
    return $real === $root || str_starts_with($real, $root . DIRECTORY_SEPARATOR);
}

/**
 * .asr files in the drop root and in each channel subdirectory.
 *
 * @return list<string>
 */
function nexrec_asrun_drop_files(string $dir): array {
    $root = realpath($dir);
    if ($root === false || !is_dir($root)) {
        return [];
    }
    $out = [];
    $names = scandir($root);
    if ($names === false) {
        return [];
    }
    foreach ($names as $name) {
        if ($name === '.' || $name === '..') {
            continue;
        }
        $path = $root . DIRECTORY_SEPARATOR . $name;
        if (is_dir($path)) {
            $children = scandir($path);
            if ($children === false) {
                continue;
            }
            foreach ($children as $child) {
                if (!preg_match('/\.asr$/i', $child)) {
                    continue;
                }
                $full = $path . DIRECTORY_SEPARATOR . $child;
                if (is_file($full) && nexrec_asrun_inside($root, $full)) {
                    $out[] = $full;
                }
            }
            continue;
        }
        if (is_file($path) && preg_match('/\.asr$/i', $name) && nexrec_asrun_inside($root, $path)) {
            $out[] = $path;
        }
    }
    sort($out);
    return $out;
}

function nexrec_asrun_one_line(string $path): string {
    if (!is_file($path)) {
        return '';
    }
    $raw = file_get_contents($path);
    if (!is_string($raw)) {
        return '';
    }
    $line = strtok($raw, "\r\n");
    return is_string($line) ? trim($line) : '';
}

/**
 * A channel folder may pin the zone, the day start, and the inputs it describes.
 *
 * @return array{timezone:string,day_start:string,input_ids:list<string>,has_inputs:bool}
 */
function nexrec_asrun_folder_config(string $folder): array {
    $tz = nexrec_asrun_one_line($folder . DIRECTORY_SEPARATOR . 'timezone');
    if ($tz === '') {
        $tz = nexrec_env_get('NEXREC_ASRUN_TZ', 'America/New_York');
    }
    if ($tz === '') {
        $tz = 'America/New_York';
    }
    $day = nexrec_asrun_one_line($folder . DIRECTORY_SEPARATOR . 'day-start');
    if ($day === '') {
        $day = nexrec_env_get('NEXREC_ASRUN_DAY_START', '04:00:00');
    }
    if ($day === '') {
        $day = '04:00:00';
    }
    $inputsPath = $folder . DIRECTORY_SEPARATOR . 'inputs';
    $ids = [];
    if (is_file($inputsPath)) {
        $lines = file($inputsPath, FILE_IGNORE_NEW_LINES);
        if (is_array($lines)) {
            foreach ($lines as $line) {
                $line = trim($line);
                if ($line === '' || str_starts_with($line, '#')) {
                    continue;
                }
                $ids[] = $line;
            }
        }
    }
    return [
        'timezone' => $tz,
        'day_start' => $day,
        'input_ids' => $ids,
        'has_inputs' => is_file($inputsPath),
    ];
}

function nexrec_asrun_file_changed(string $filename, int $mtime): bool {
    $st = nexrec_db()->prepare('SELECT updated_at FROM asruns WHERE filename = :f ORDER BY updated_at DESC LIMIT 1');
    $st->bindValue(':f', $filename, SQLITE3_TEXT);
    $row = nexrec_row($st->execute());
    if ($row === null) {
        return true;
    }
    $updated = strtotime((string) $row['updated_at']);
    if ($updated === false) {
        return true;
    }
    return $mtime > $updated;
}

/**
 * Import new or replaced .asr files. Unchanged files are left alone.
 * A folder inputs file replaces that log's input links. Without one, links stay.
 *
 * @return array{imported:int,skipped:int,errors:list<string>}
 */
function nexrec_asrun_import_tree(?string $dir = null): array {
    $dir = $dir ?? nexrec_asrun_drop_dir();
    $imported = 0;
    $skipped = 0;
    $errors = [];
    if (!is_dir($dir)) {
        return ['imported' => 0, 'skipped' => 0, 'errors' => ["as-run drop folder is missing: {$dir}"]];
    }
    $root = realpath($dir);
    if ($root === false) {
        return ['imported' => 0, 'skipped' => 0, 'errors' => ["as-run drop folder is missing: {$dir}"]];
    }
    foreach (nexrec_asrun_drop_files($root) as $path) {
        $rel = nexrec_asrun_rel($root, $path);
        $mtime = filemtime($path);
        if ($mtime === false) {
            $errors[] = "{$rel}: could not read mtime";
            continue;
        }
        if (!nexrec_asrun_file_changed($rel, $mtime)) {
            $skipped++;
            continue;
        }
        $text = file_get_contents($path);
        if (!is_string($text) || $text === '') {
            $errors[] = "{$rel}: empty file";
            continue;
        }
        $cfg = nexrec_asrun_folder_config(dirname($path));
        $opts = [
            'filename' => $rel,
            'timezone' => $cfg['timezone'],
            'day_start' => $cfg['day_start'],
        ];
        if ($cfg['has_inputs']) {
            $opts['input_ids'] = $cfg['input_ids'];
        }
        try {
            nexrec_asrun_import($text, $opts);
            $imported++;
            echo "imported {$rel}\n";
        } catch (Throwable $e) {
            $errors[] = "{$rel}: " . $e->getMessage();
        }
    }
    return ['imported' => $imported, 'skipped' => $skipped, 'errors' => $errors];
}

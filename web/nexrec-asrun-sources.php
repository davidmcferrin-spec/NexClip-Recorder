<?php
/**
 * Scheduled as-run sources. The hourly cron lists each enabled source and
 * imports .asr files that are new or newer. Passwords use NEXREC_DEST_KEY.
 */
declare(strict_types=1);

function nexrec_asrun_source_extra(mixed $raw): array {
    if (is_array($raw)) {
        return $raw;
    }
    if (!is_string($raw) || $raw === '') {
        return [];
    }
    $parsed = json_decode($raw, true);
    return is_array($parsed) ? $parsed : [];
}

/** @param array<string,mixed> $row */
function nexrec_asrun_source_public(array $row): array {
    $extra = nexrec_asrun_source_extra($row['extra'] ?? '{}');
    $keyPath = (string) ($extra['key_path'] ?? '');
    $ids = json_decode((string) ($row['input_ids'] ?? '[]'), true);
    if (!is_array($ids)) {
        $ids = [];
    }
    $cleanIds = [];
    foreach ($ids as $id) {
        if (is_string($id) && $id !== '') {
            $cleanIds[] = $id;
        }
    }
    return [
        'id' => (string) ($row['id'] ?? ''),
        'name' => (string) ($row['name'] ?? ''),
        'protocol' => (string) ($row['protocol'] ?? ''),
        'host' => (string) ($row['host'] ?? ''),
        'port' => isset($row['port']) && $row['port'] !== null && $row['port'] !== '' ? (int) $row['port'] : null,
        'remote_prefix' => (string) ($row['remote_prefix'] ?? ''),
        'username' => (string) ($row['username'] ?? ''),
        'enabled' => (int) ($row['enabled'] ?? 0) === 1 ? 1 : 0,
        'has_secret' => trim((string) ($row['secret_cipher'] ?? '')) !== '',
        'timezone' => (string) ($row['timezone'] ?? 'America/New_York'),
        'day_start' => (string) ($row['day_start'] ?? '04:00:00'),
        'input_ids' => $cleanIds,
        'last_error' => (string) ($row['last_error'] ?? ''),
        'last_imported' => (int) ($row['last_imported'] ?? 0),
        'last_scan' => (string) ($row['last_scan'] ?? ''),
        'extra' => [
            'bucket' => (string) ($extra['bucket'] ?? ''),
            'region' => (string) ($extra['region'] ?? ''),
            'share' => (string) ($extra['share'] ?? ''),
            'domain' => (string) ($extra['domain'] ?? ''),
            'has_key' => $keyPath !== '' && is_file($keyPath) ? 1 : 0,
            'has_key_pass' => trim((string) ($extra['key_pass_cipher'] ?? '')) !== '' ? 1 : 0,
            'path_style' => (int) ($extra['path_style'] ?? 0) === 1 ? 1 : 0,
            'explicit_tls' => (int) ($extra['explicit_tls'] ?? 0) === 1 ? 1 : 0,
        ],
    ];
}

function nexrec_asrun_local_folder(string $path): string {
    $path = trim(str_replace('\\', '/', $path));
    if ($path === '' || $path === '/' || strlen($path) > 400 || str_contains($path, "\0")) {
        throw new InvalidArgumentException('folder is not allowed');
    }
    if (!str_starts_with($path, '/')) {
        throw new InvalidArgumentException('folder must be an absolute path');
    }
    foreach (explode('/', $path) as $part) {
        if ($part === '..') {
            throw new InvalidArgumentException('folder is not allowed');
        }
    }
    return rtrim($path, '/');
}

function nexrec_asrun_rel_ok(string $rel): bool {
    if ($rel === '' || strlen($rel) > 400 || str_contains($rel, "\0") || str_contains($rel, '\\')) {
        return false;
    }
    if (!preg_match('/\.asr$/i', $rel)) {
        return false;
    }
    foreach (explode('/', $rel) as $part) {
        if ($part === '' || $part === '.' || $part === '..') {
            return false;
        }
        if (preg_match('/^[A-Za-z0-9._@ -]{1,200}$/', $part) !== 1) {
            return false;
        }
    }
    return true;
}

/**
 * rclone lsf --format tp --separator |
 *
 * @return list<array{path:string,mtime:int}>
 */
function nexrec_asrun_parse_lsf(string $text): array {
    $out = [];
    foreach (preg_split("/\r\n|\n|\r/", $text) ?: [] as $line) {
        $line = trim($line);
        if ($line === '' || !str_contains($line, '|')) {
            continue;
        }
        [$time, $path] = explode('|', $line, 2);
        $path = str_replace('\\', '/', trim($path));
        if (!nexrec_asrun_rel_ok($path)) {
            continue;
        }
        $mtime = strtotime(trim($time));
        $out[] = ['path' => $path, 'mtime' => $mtime === false ? time() : $mtime];
    }
    return $out;
}

/**
 * @param array<string,mixed> $body
 * @param array<string,mixed>|null $existing
 * @return array<string,mixed>
 */
function nexrec_asrun_source_normalize(array $body, ?array $existing): array {
    require_once __DIR__ . '/nexrec-deliver.php';
    $name = trim((string) ($body['name'] ?? ''));
    $name = preg_replace('/[\r\n\t]+/', ' ', $name) ?? '';
    if ($name === '' || strlen($name) > 80) {
        throw new InvalidArgumentException('name is required');
    }
    $protocol = strtolower(trim((string) ($body['protocol'] ?? '')));
    if (!in_array($protocol, ['local', 'sftp', 'ftp', 's3', 'smb'], true)) {
        throw new InvalidArgumentException('protocol must be local, sftp, ftp, s3, or smb');
    }
    $tz = trim((string) ($body['timezone'] ?? 'America/New_York'));
    if ($tz === '') {
        $tz = 'America/New_York';
    }
    if (!in_array($tz, timezone_identifiers_list(), true)) {
        throw new InvalidArgumentException('unknown timezone');
    }
    $dayStart = trim((string) ($body['day_start'] ?? '04:00:00'));
    if ($dayStart === '') {
        $dayStart = '04:00:00';
    }
    if (!preg_match('/^\d{2}:\d{2}:\d{2}$/', $dayStart)) {
        throw new InvalidArgumentException('day start must be HH:MM:SS');
    }
    $rawIds = $body['input_ids'] ?? [];
    if (!is_array($rawIds)) {
        throw new InvalidArgumentException('input_ids must be a list');
    }
    $inputIds = [];
    foreach ($rawIds as $raw) {
        $id = trim((string) $raw);
        if ($id === '' || in_array($id, $inputIds, true)) {
            continue;
        }
        if (!preg_match('/^[a-z0-9][a-z0-9-]{0,31}$/', $id)) {
            throw new InvalidArgumentException('invalid input id');
        }
        $inputIds[] = $id;
    }
    if (count($inputIds) > 20) {
        throw new InvalidArgumentException('too many inputs');
    }
    $host = '';
    $port = null;
    $username = '';
    $prefix = '';
    $bucket = '';
    $region = '';
    $share = '';
    $domain = '';
    $pathStyle = 0;
    $explicitTls = 0;
    $cipher = $existing !== null ? (string) ($existing['secret_cipher'] ?? '') : '';
    if ($protocol === 'local') {
        $prefix = nexrec_asrun_local_folder((string) ($body['remote_prefix'] ?? $body['local_path'] ?? ''));
        $cipher = '';
    } else {
        $host = trim((string) ($body['host'] ?? ''));
        if ($host !== '' && preg_match('/^[A-Za-z0-9._:-]{1,255}$/', $host) !== 1) {
            throw new InvalidArgumentException('host is not allowed');
        }
        if (in_array($protocol, ['sftp', 'ftp', 'smb'], true) && $host === '') {
            throw new InvalidArgumentException('host is required');
        }
        $portIn = $body['port'] ?? null;
        if ($portIn !== '' && $portIn !== null) {
            if (!is_numeric($portIn)) {
                throw new InvalidArgumentException('port is not allowed');
            }
            $port = (int) $portIn;
            if ($port < 1 || $port > 65535) {
                throw new InvalidArgumentException('port is not allowed');
            }
        }
        $username = trim((string) ($body['username'] ?? ''));
        if ($username === '') {
            throw new InvalidArgumentException($protocol === 's3' ? 'access key is required' : 'username is required');
        }
        if (strlen($username) > 128 || strpbrk($username, "\r\n") !== false) {
            throw new InvalidArgumentException('username is not allowed');
        }
        $prefix = nexrec_deliver_check_prefix((string) ($body['remote_prefix'] ?? ''));
        $incoming = nexrec_asrun_source_extra($body['extra'] ?? []);
        $bucket = trim((string) ($incoming['bucket'] ?? ''));
        $region = trim((string) ($incoming['region'] ?? ''));
        $share = trim((string) ($incoming['share'] ?? ''));
        $domain = trim((string) ($incoming['domain'] ?? ''));
        $pathStyle = !empty($incoming['path_style']) ? 1 : 0;
        $explicitTls = ($protocol === 'ftp' && !empty($incoming['explicit_tls'])) ? 1 : 0;
        if ($protocol === 's3') {
            $bucket = nexrec_deliver_check_segment($bucket, 'bucket');
            if ($region === '') {
                $region = 'us-east-1';
            }
            if (preg_match('/^[A-Za-z0-9-]{1,64}$/', $region) !== 1) {
                throw new InvalidArgumentException('region is not allowed');
            }
        } else {
            $bucket = '';
            $region = '';
            $pathStyle = 0;
        }
        if ($protocol === 'smb') {
            $share = nexrec_deliver_check_segment($share, 'share');
            if ($domain !== '' && preg_match('/^[A-Za-z0-9._-]{1,64}$/', $domain) !== 1) {
                throw new InvalidArgumentException('domain is not allowed');
            }
        } else {
            $share = '';
            $domain = '';
        }
        $secret = (string) ($body['secret'] ?? '');
        if (strpbrk($secret, "\r\n\0") !== false || strlen($secret) > 512) {
            throw new InvalidArgumentException('password is not allowed');
        }
        if ($secret !== '') {
            $cipher = nexrec_secret_encrypt($secret, nexrec_dest_key());
        } elseif ($cipher === '' && $protocol !== 'sftp') {
            throw new InvalidArgumentException('password is required');
        }
    }
    $enabled = array_key_exists('enabled', $body) ? (!empty($body['enabled']) ? 1 : 0) : 1;
    return [
        'name' => $name,
        'protocol' => $protocol,
        'host' => $host,
        'port' => $port,
        'remote_prefix' => $prefix,
        'username' => $username,
        'secret_cipher' => $cipher,
        'timezone' => $tz,
        'day_start' => $dayStart,
        'input_ids' => $inputIds,
        'enabled' => $enabled,
        'extra' => [
            'bucket' => $bucket,
            'region' => $region,
            'share' => $share,
            'domain' => $domain,
            'path_style' => $pathStyle,
            'explicit_tls' => $explicitTls,
        ],
    ];
}

/** @param list<string> $ids */
function nexrec_asrun_source_check_inputs(array $ids): void {
    foreach ($ids as $inputId) {
        $st = nexrec_db()->prepare('SELECT id FROM inputs WHERE id = :i');
        $st->bindValue(':i', $inputId, SQLITE3_TEXT);
        if (nexrec_row($st->execute()) === null) {
            throw new InvalidArgumentException('unknown input');
        }
    }
}

/** @param array<string,mixed> $body */
function nexrec_asrun_source_save(array $body, string $username): array {
    require_once __DIR__ . '/nexrec-deliver.php';
    $id = trim((string) ($body['id'] ?? ''));
    $existing = null;
    if ($id !== '') {
        if (preg_match('/^asi_[a-f0-9]{12}$/', $id) !== 1) {
            throw new InvalidArgumentException('invalid id');
        }
        $st = nexrec_db()->prepare('SELECT * FROM asrun_sources WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $existing = nexrec_row($st->execute());
        if ($existing === null) {
            throw new InvalidArgumentException('source not found');
        }
    } else {
        $id = nexrec_new_id('asi');
    }
    $norm = nexrec_asrun_source_normalize($body, $existing);
    nexrec_asrun_source_check_inputs($norm['input_ids']);
    $extra = $norm['extra'];
    $pem = (string) ($body['private_key'] ?? '');
    $keyPass = (string) ($body['key_pass'] ?? '');
    if (strpbrk($keyPass, "\r\n\0") !== false || strlen($keyPass) > 512) {
        throw new InvalidArgumentException('key passphrase is not allowed');
    }
    if ($norm['protocol'] === 'sftp') {
        $previous = $existing !== null ? nexrec_asrun_source_extra($existing['extra'] ?? '{}') : [];
        if ($pem !== '') {
            $extra['key_path'] = nexrec_dest_store_key($id, $pem);
            if ($keyPass === '') {
                unset($extra['key_pass_cipher']);
            }
        } else {
            $kept = (string) ($previous['key_path'] ?? '');
            if ($kept !== '' && $kept === nexrec_dest_key_path($id) && is_file($kept)) {
                $extra['key_path'] = $kept;
            }
            if ($keyPass === '' && !empty($previous['key_pass_cipher'])) {
                $extra['key_pass_cipher'] = (string) $previous['key_pass_cipher'];
            }
        }
        if ($keyPass !== '') {
            $extra['key_pass_cipher'] = nexrec_secret_encrypt($keyPass, nexrec_dest_key());
        }
        if ($norm['secret_cipher'] === '' && empty($extra['key_path'])) {
            throw new InvalidArgumentException('password or private key is required');
        }
    } else {
        if (preg_match('/^asi_[a-f0-9]{12}$/', $id) === 1) {
            nexrec_dest_remove_key($id);
        }
    }
    $now = nexrec_now_iso();
    $extraJson = json_encode($extra, JSON_UNESCAPED_SLASHES);
    $idsJson = json_encode($norm['input_ids'], JSON_UNESCAPED_SLASHES);
    if ($existing === null) {
        $st = nexrec_db()->prepare(
            'INSERT INTO asrun_sources
             (id,name,protocol,host,port,remote_prefix,username,secret_cipher,extra,timezone,day_start,input_ids,enabled,created_by,created_at,updated_at)
             VALUES (:id,:name,:protocol,:host,:port,:prefix,:user,:cipher,:extra,:tz,:day,:ids,:enabled,:by,:c,:u)'
        );
        $st->bindValue(':by', $username, SQLITE3_TEXT);
        $st->bindValue(':c', $now, SQLITE3_TEXT);
    } else {
        $st = nexrec_db()->prepare(
            'UPDATE asrun_sources SET name=:name, protocol=:protocol, host=:host, port=:port, remote_prefix=:prefix,
             username=:user, secret_cipher=:cipher, extra=:extra, timezone=:tz, day_start=:day, input_ids=:ids,
             enabled=:enabled, updated_at=:u WHERE id=:id'
        );
    }
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    $st->bindValue(':name', $norm['name'], SQLITE3_TEXT);
    $st->bindValue(':protocol', $norm['protocol'], SQLITE3_TEXT);
    $st->bindValue(':host', $norm['host'], SQLITE3_TEXT);
    if ($norm['port'] === null) {
        $st->bindValue(':port', null, SQLITE3_NULL);
    } else {
        $st->bindValue(':port', $norm['port'], SQLITE3_INTEGER);
    }
    $st->bindValue(':prefix', $norm['remote_prefix'], SQLITE3_TEXT);
    $st->bindValue(':user', $norm['username'], SQLITE3_TEXT);
    $st->bindValue(':cipher', $norm['secret_cipher'], SQLITE3_TEXT);
    $st->bindValue(':extra', $extraJson === false ? '{}' : $extraJson, SQLITE3_TEXT);
    $st->bindValue(':tz', $norm['timezone'], SQLITE3_TEXT);
    $st->bindValue(':day', $norm['day_start'], SQLITE3_TEXT);
    $st->bindValue(':ids', $idsJson === false ? '[]' : $idsJson, SQLITE3_TEXT);
    $st->bindValue(':enabled', $norm['enabled'], SQLITE3_INTEGER);
    $st->bindValue(':u', $now, SQLITE3_TEXT);
    $st->execute();
    $got = nexrec_db()->prepare('SELECT * FROM asrun_sources WHERE id=:id');
    $got->bindValue(':id', $id, SQLITE3_TEXT);
    $row = nexrec_row($got->execute());
    if ($row === null) {
        throw new RuntimeException('source was not saved');
    }
    return nexrec_asrun_source_public($row);
}

/** @return list<array<string,mixed>> */
function nexrec_asrun_sources_public(): array {
    $res = nexrec_db()->query('SELECT * FROM asrun_sources ORDER BY name ASC');
    $out = [];
    while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
        $out[] = nexrec_asrun_source_public($row);
    }
    return $out;
}

function nexrec_asrun_source_delete(string $id): void {
    require_once __DIR__ . '/nexrec-deliver.php';
    if (preg_match('/^asi_[a-f0-9]{12}$/', $id) !== 1) {
        throw new InvalidArgumentException('invalid id');
    }
    $del = nexrec_db()->prepare('DELETE FROM asrun_sources WHERE id=:id');
    $del->bindValue(':id', $id, SQLITE3_TEXT);
    $del->execute();
    nexrec_dest_remove_key($id);
}

function nexrec_asrun_ini(string $value): string {
    if ($value === '') {
        return '""';
    }
    if (preg_match('/[ #"\'\\\\]/', $value) === 1) {
        return '"' . str_replace(['\\', '"'], ['\\\\', '\\"'], $value) . '"';
    }
    return $value;
}

/** @param array<string,mixed> $row */
function nexrec_asrun_rclone_config(array $row, string $obscured, string $keyObscured): string {
    $proto = (string) ($row['protocol'] ?? '');
    $extra = nexrec_asrun_source_extra($row['extra'] ?? '{}');
    $lines = ['[src]', 'type = ' . $proto];
    $host = (string) ($row['host'] ?? '');
    $user = (string) ($row['username'] ?? '');
    $port = $row['port'] ?? null;
    if ($proto === 'sftp') {
        $lines[] = 'host = ' . nexrec_asrun_ini($host);
        if ($user !== '') {
            $lines[] = 'user = ' . nexrec_asrun_ini($user);
        }
        if ($port) {
            $lines[] = 'port = ' . (int) $port;
        }
        if ($obscured !== '') {
            $lines[] = 'pass = ' . nexrec_asrun_ini($obscured);
        }
        $keyPath = (string) ($extra['key_path'] ?? '');
        if ($keyPath !== '') {
            $lines[] = 'key_file = ' . nexrec_asrun_ini($keyPath);
        }
        if ($keyObscured !== '') {
            $lines[] = 'key_file_pass = ' . nexrec_asrun_ini($keyObscured);
        }
    } elseif ($proto === 'ftp') {
        $lines[] = 'host = ' . nexrec_asrun_ini($host);
        if ($user !== '') {
            $lines[] = 'user = ' . nexrec_asrun_ini($user);
        }
        if ($port) {
            $lines[] = 'port = ' . (int) $port;
        }
        if ($obscured !== '') {
            $lines[] = 'pass = ' . nexrec_asrun_ini($obscured);
        }
        if ((int) ($extra['explicit_tls'] ?? 0) === 1) {
            $lines[] = 'explicit_tls = true';
        }
    } elseif ($proto === 's3') {
        $lines[] = 'provider = ' . ($host !== '' ? 'Other' : 'AWS');
        if ($user !== '') {
            $lines[] = 'access_key_id = ' . nexrec_asrun_ini($user);
        }
        if ($obscured !== '') {
            $lines[] = 'secret_access_key = ' . nexrec_asrun_ini($obscured);
        }
        $lines[] = 'region = ' . nexrec_asrun_ini((string) ($extra['region'] ?? 'us-east-1'));
        if ($host !== '') {
            $lines[] = 'endpoint = ' . nexrec_asrun_ini($host);
        }
        if ((int) ($extra['path_style'] ?? 0) === 1 || $host !== '') {
            $lines[] = 'force_path_style = true';
        }
    } elseif ($proto === 'smb') {
        $lines[] = 'host = ' . nexrec_asrun_ini($host);
        $domain = (string) ($extra['domain'] ?? '');
        $smbUser = ($domain !== '' && $user !== '') ? ($domain . '\\' . $user) : $user;
        if ($smbUser !== '') {
            $lines[] = 'user = ' . nexrec_asrun_ini($smbUser);
        }
        if ($obscured !== '') {
            $lines[] = 'pass = ' . nexrec_asrun_ini($obscured);
        }
        if ($port) {
            $lines[] = 'port = ' . (int) $port;
        }
    } else {
        throw new InvalidArgumentException('unknown protocol');
    }
    return implode("\n", $lines) . "\n";
}

/** @param array<string,mixed> $row */
function nexrec_asrun_remote_root(array $row): string {
    $proto = (string) ($row['protocol'] ?? '');
    $extra = nexrec_asrun_source_extra($row['extra'] ?? '{}');
    $bits = [];
    if ($proto === 's3') {
        $bits[] = nexrec_deliver_check_segment((string) ($extra['bucket'] ?? ''), 'bucket');
    } elseif ($proto === 'smb') {
        $bits[] = nexrec_deliver_check_segment((string) ($extra['share'] ?? ''), 'share');
    } elseif (!in_array($proto, ['sftp', 'ftp'], true)) {
        throw new InvalidArgumentException('unknown protocol');
    }
    $prefix = nexrec_deliver_check_prefix((string) ($row['remote_prefix'] ?? ''));
    if ($prefix !== '') {
        foreach (explode('/', $prefix) as $part) {
            $bits[] = $part;
        }
    }
    return 'src:' . implode('/', $bits);
}

/**
 * @param list<string> $cmd
 * @return array{0:int,1:string,2:string}
 */
function nexrec_asrun_proc(array $cmd, string $stdin, int $timeout): array {
    $code = 1;
    $proc = proc_open($cmd, [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($proc)) {
        throw new RuntimeException('could not start rclone');
    }
    fwrite($pipes[0], $stdin);
    fclose($pipes[0]);
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $out = '';
    $err = '';
    $deadline = microtime(true) + $timeout;
    while (true) {
        $read = [$pipes[1], $pipes[2]];
        $write = null;
        $except = null;
        $left = $deadline - microtime(true);
        if ($left <= 0) {
            proc_terminate($proc);
            fclose($pipes[1]);
            fclose($pipes[2]);
            proc_close($proc);
            throw new RuntimeException('rclone timed out');
        }
        stream_select($read, $write, $except, (int) min(1, ceil($left)));
        foreach ($read as $pipe) {
            $chunk = stream_get_contents($pipe);
            if (!is_string($chunk)) {
                continue;
            }
            if ($pipe === $pipes[1]) {
                $out .= $chunk;
            } else {
                $err .= $chunk;
            }
        }
        $status = proc_get_status($proc);
        if (!$status['running']) {
            $code = (int) $status['exitcode'];
            break;
        }
    }
    $restOut = stream_get_contents($pipes[1]);
    $restErr = stream_get_contents($pipes[2]);
    if (is_string($restOut)) {
        $out .= $restOut;
    }
    if (is_string($restErr)) {
        $err .= $restErr;
    }
    fclose($pipes[1]);
    fclose($pipes[2]);
    proc_close($proc);
    return [$code, $out, $err];
}

function nexrec_asrun_obscure(string $rclone, string $password): string {
    if ($password === '') {
        return '';
    }
    [$code, $out, $err] = nexrec_asrun_proc([$rclone, 'obscure', '-'], $password, 20);
    if ($code !== 0) {
        $msg = trim($err);
        throw new RuntimeException($msg !== '' ? substr($msg, 0, 300) : 'rclone obscure failed');
    }
    $token = trim($out);
    if ($token === '' || str_contains($token, "\n")) {
        throw new RuntimeException('rclone obscure returned nothing');
    }
    return $token;
}

function nexrec_asrun_source_note(string $id, string $error, int $imported): void {
    $st = nexrec_db()->prepare(
        'UPDATE asrun_sources SET last_error=:e, last_imported=:n, last_scan=:t WHERE id=:id'
    );
    $st->bindValue(':e', substr($error, 0, 500), SQLITE3_TEXT);
    $st->bindValue(':n', $imported, SQLITE3_INTEGER);
    $st->bindValue(':t', nexrec_now_iso(), SQLITE3_TEXT);
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    $st->execute();
}

/**
 * @param array<string,mixed> $row
 * @return array{imported:int,skipped:int,errors:list<string>}
 */
function nexrec_asrun_import_local(array $row): array {
    $dir = (string) ($row['remote_prefix'] ?? '');
    $imported = 0;
    $skipped = 0;
    $errors = [];
    if (!is_dir($dir)) {
        return ['imported' => 0, 'skipped' => 0, 'errors' => ['folder is missing']];
    }
    $id = (string) $row['id'];
    $ids = json_decode((string) ($row['input_ids'] ?? '[]'), true);
    $link = is_array($ids) && $ids !== [];
    foreach (nexrec_asrun_drop_files($dir) as $path) {
        $rel = nexrec_asrun_rel((string) realpath($dir), $path);
        if (!nexrec_asrun_rel_ok($rel)) {
            $errors[] = $rel . ': name is not allowed';
            continue;
        }
        $mtime = filemtime($path);
        if ($mtime === false) {
            $errors[] = $rel . ': could not read mtime';
            continue;
        }
        $stored = $id . '/' . $rel;
        if (!nexrec_asrun_file_changed($stored, $mtime)) {
            $skipped++;
            continue;
        }
        $size = filesize($path);
        if ($size === false || $size > 2000000) {
            $errors[] = $rel . ': file is too large';
            continue;
        }
        $text = file_get_contents($path);
        if (!is_string($text) || $text === '') {
            $errors[] = $rel . ': empty file';
            continue;
        }
        $opts = [
            'filename' => $stored,
            'timezone' => (string) ($row['timezone'] ?? 'America/New_York'),
            'day_start' => (string) ($row['day_start'] ?? '04:00:00'),
        ];
        if ($link) {
            $opts['input_ids'] = $ids;
        }
        try {
            nexrec_asrun_import($text, $opts);
            $imported++;
            echo "imported {$stored}\n";
        } catch (Throwable $e) {
            $errors[] = $rel . ': ' . $e->getMessage();
        }
        if ($imported >= 50) {
            $errors[] = 'stopped after 50 new files';
            break;
        }
    }
    return ['imported' => $imported, 'skipped' => $skipped, 'errors' => $errors];
}

/**
 * @param array<string,mixed> $row
 * @return array{imported:int,skipped:int,errors:list<string>}
 */
function nexrec_asrun_import_remote(array $row): array {
    require_once __DIR__ . '/nexrec-deliver.php';
    $rclone = nexrec_env_get('NEXREC_RCLONE', 'rclone');
    if ($rclone === '') {
        $rclone = 'rclone';
    }
    $cipher = (string) ($row['secret_cipher'] ?? '');
    $extra = nexrec_asrun_source_extra($row['extra'] ?? '{}');
    $plain = $cipher !== '' ? nexrec_secret_decrypt($cipher, nexrec_dest_key()) : '';
    $keyCipher = (string) ($extra['key_pass_cipher'] ?? '');
    $keyPlain = $keyCipher !== '' ? nexrec_secret_decrypt($keyCipher, nexrec_dest_key()) : '';
    $obscured = nexrec_asrun_obscure($rclone, $plain);
    $keyObscured = nexrec_asrun_obscure($rclone, $keyPlain);
    $config = nexrec_asrun_rclone_config($row, $obscured, $keyObscured);
    $tmp = tempnam(sys_get_temp_dir(), 'nexrec-asr-');
    if ($tmp === false) {
        throw new RuntimeException('could not write rclone config');
    }
    $cfgPath = $tmp . '.conf';
    @unlink($tmp);
    if (file_put_contents($cfgPath, $config) === false) {
        throw new RuntimeException('could not write rclone config');
    }
    chmod($cfgPath, 0600);
    $imported = 0;
    $skipped = 0;
    $errors = [];
    try {
        $root = nexrec_asrun_remote_root($row);
        [$code, $out, $err] = nexrec_asrun_proc([
            $rclone, 'lsf', $root,
            '--config', $cfgPath,
            '--files-only',
            '--recursive',
            '--max-depth', '2',
            '--include', '*.asr',
            '--include', '*.ASR',
            '--format', 'tp',
            '--separator', '|',
        ], '', 60);
        if ($code !== 0) {
            $msg = trim($err);
            throw new RuntimeException($msg !== '' ? substr($msg, 0, 300) : 'rclone list failed');
        }
        $id = (string) $row['id'];
        $ids = json_decode((string) ($row['input_ids'] ?? '[]'), true);
        $link = is_array($ids) && $ids !== [];
        $scratch = rtrim(nexrec_data_dir(), '/\\') . '/tmp/asrun-pull';
        if (!is_dir($scratch) && !mkdir($scratch, 0700, true) && !is_dir($scratch)) {
            throw new RuntimeException('could not create scratch');
        }
        foreach (nexrec_asrun_parse_lsf($out) as $item) {
            $rel = $item['path'];
            $stored = $id . '/' . $rel;
            if (!nexrec_asrun_file_changed($stored, $item['mtime'])) {
                $skipped++;
                continue;
            }
            if ($imported >= 50) {
                $errors[] = 'stopped after 50 new files';
                break;
            }
            $dest = $scratch . '/' . bin2hex(random_bytes(8)) . '.asr';
            $remote = rtrim($root, '/') === 'src:' ? ('src:' . $rel) : (rtrim($root, '/') . '/' . $rel);
            [$copyCode, , $copyErr] = nexrec_asrun_proc([
                $rclone, 'copyto', $remote, $dest,
                '--config', $cfgPath,
            ], '', 60);
            if ($copyCode !== 0 || !is_file($dest)) {
                $errors[] = $rel . ': ' . (trim($copyErr) !== '' ? substr(trim($copyErr), 0, 200) : 'copy failed');
                if (is_file($dest)) {
                    unlink($dest);
                }
                continue;
            }
            $size = filesize($dest);
            $text = ($size !== false && $size <= 2000000) ? file_get_contents($dest) : false;
            unlink($dest);
            if (!is_string($text) || $text === '') {
                $errors[] = $rel . ': empty or too large';
                continue;
            }
            $opts = [
                'filename' => $stored,
                'timezone' => (string) ($row['timezone'] ?? 'America/New_York'),
                'day_start' => (string) ($row['day_start'] ?? '04:00:00'),
            ];
            if ($link) {
                $opts['input_ids'] = $ids;
            }
            try {
                nexrec_asrun_import($text, $opts);
                $imported++;
                echo "imported {$stored}\n";
            } catch (Throwable $e) {
                $errors[] = $rel . ': ' . $e->getMessage();
            }
        }
    } finally {
        if (is_file($cfgPath)) {
            unlink($cfgPath);
        }
    }
    return ['imported' => $imported, 'skipped' => $skipped, 'errors' => $errors];
}

/**
 * @return array{imported:int,skipped:int,errors:list<string>}
 */
function nexrec_asrun_import_sources(): array {
    $imported = 0;
    $skipped = 0;
    $errors = [];
    $res = nexrec_db()->query("SELECT * FROM asrun_sources WHERE enabled=1 ORDER BY name ASC");
    while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
        $id = (string) $row['id'];
        $name = (string) ($row['name'] ?? $id);
        try {
            $result = (string) ($row['protocol'] ?? '') === 'local'
                ? nexrec_asrun_import_local($row)
                : nexrec_asrun_import_remote($row);
        } catch (Throwable $e) {
            $result = ['imported' => 0, 'skipped' => 0, 'errors' => [$e->getMessage()]];
        }
        $imported += (int) $result['imported'];
        $skipped += (int) $result['skipped'];
        $note = implode('; ', $result['errors']);
        foreach ($result['errors'] as $err) {
            $errors[] = $name . ': ' . $err;
        }
        nexrec_asrun_source_note($id, $note, (int) $result['imported']);
    }
    return ['imported' => $imported, 'skipped' => $skipped, 'errors' => $errors];
}

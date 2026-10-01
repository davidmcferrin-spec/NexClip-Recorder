<?php
/**
 * Global send-to destinations and the transfer queue.
 * Passwords are ciphertext (nexrec-secret.php). List responses never include them.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';
require_once __DIR__ . '/nexrec-secret.php';

function nexrec_deliver_now(): string {
    return gmdate('Y-m-d\TH:i:s\Z');
}

function nexrec_deliver_extra(mixed $raw): array {
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
function nexrec_destination_public(array $row): array {
    $extra = nexrec_deliver_extra($row['extra'] ?? '{}');
    $publicExtra = [
        'bucket' => (string) ($extra['bucket'] ?? ''),
        'region' => (string) ($extra['region'] ?? ''),
        'share' => (string) ($extra['share'] ?? ''),
        'domain' => (string) ($extra['domain'] ?? ''),
        'key_path' => (string) ($extra['key_path'] ?? ''),
        'path_style' => (int) ($extra['path_style'] ?? 0) === 1 ? 1 : 0,
    ];
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
        'extra' => $publicExtra,
        'created_by' => (string) ($row['created_by'] ?? ''),
        'created_at' => (string) ($row['created_at'] ?? ''),
        'updated_at' => (string) ($row['updated_at'] ?? ''),
    ];
}

function nexrec_deliver_check_segment(string $value, string $label): string {
    $value = trim($value);
    if ($value === '' || preg_match('/^[A-Za-z0-9._@-]{1,200}$/', $value) !== 1) {
        throw new InvalidArgumentException($label . ' is not allowed');
    }
    return $value;
}

function nexrec_deliver_check_prefix(string $prefix): string {
    $prefix = trim(str_replace('\\', '/', $prefix), '/');
    if ($prefix === '') {
        return '';
    }
    $parts = [];
    foreach (explode('/', $prefix) as $part) {
        $part = trim($part);
        if ($part === '' || $part === '.') {
            continue;
        }
        if ($part === '..' || preg_match('/^[A-Za-z0-9._@-]{1,200}$/', $part) !== 1) {
            throw new InvalidArgumentException('remote prefix is not allowed');
        }
        $parts[] = $part;
    }
    return implode('/', $parts);
}

/** @param array<string,mixed> $body */
function nexrec_destination_normalize(array $body, ?array $existing): array {
    $name = trim((string) ($body['name'] ?? ''));
    $name = preg_replace('/[\r\n\t]+/', ' ', $name) ?? '';
    if ($name === '' || strlen($name) > 80) {
        throw new InvalidArgumentException('name is required');
    }
    $protocol = strtolower(trim((string) ($body['protocol'] ?? '')));
    if (!in_array($protocol, ['sftp', 's3', 'smb'], true)) {
        throw new InvalidArgumentException('protocol must be sftp, s3, or smb');
    }
    $host = trim((string) ($body['host'] ?? ''));
    if ($host !== '' && preg_match('/^[A-Za-z0-9._:-]{1,255}$/', $host) !== 1) {
        throw new InvalidArgumentException('host is not allowed');
    }
    if (($protocol === 'sftp' || $protocol === 'smb') && $host === '') {
        throw new InvalidArgumentException('host is required');
    }
    $port = $body['port'] ?? null;
    if ($port === '' || $port === null) {
        $port = null;
    } else {
        if (!is_numeric($port)) {
            throw new InvalidArgumentException('port is not allowed');
        }
        $port = (int) $port;
        if ($port < 1 || $port > 65535) {
            throw new InvalidArgumentException('port is not allowed');
        }
    }
    $username = trim((string) ($body['username'] ?? ''));
    if (strlen($username) > 128 || strpbrk($username, "\r\n") !== false) {
        throw new InvalidArgumentException('username is not allowed');
    }
    $prefix = nexrec_deliver_check_prefix((string) ($body['remote_prefix'] ?? ''));
    $incoming = nexrec_deliver_extra($body['extra'] ?? []);
    $bucket = trim((string) ($incoming['bucket'] ?? ''));
    $region = trim((string) ($incoming['region'] ?? ''));
    $share = trim((string) ($incoming['share'] ?? ''));
    $domain = trim((string) ($incoming['domain'] ?? ''));
    $keyPath = trim((string) ($incoming['key_path'] ?? ''));
    $pathStyle = !empty($incoming['path_style']) ? 1 : 0;
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
    if ($protocol === 'sftp') {
        if ($keyPath !== '') {
            if (strlen($keyPath) > 512 || strpbrk($keyPath, "\r\n\0") !== false || !str_starts_with($keyPath, '/')) {
                throw new InvalidArgumentException('key path must be an absolute path');
            }
        }
    } else {
        $keyPath = '';
    }
    $secret = (string) ($body['secret'] ?? '');
    if (strpbrk($secret, "\r\n\0") !== false || strlen($secret) > 512) {
        throw new InvalidArgumentException('password is not allowed');
    }
    $cipher = $existing !== null ? (string) ($existing['secret_cipher'] ?? '') : '';
    if ($secret !== '') {
        $cipher = nexrec_secret_encrypt($secret, nexrec_dest_key());
    } elseif ($cipher === '' && !($protocol === 'sftp' && $keyPath !== '')) {
        throw new InvalidArgumentException('password is required');
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
        'extra' => json_encode([
            'bucket' => $bucket,
            'region' => $region,
            'share' => $share,
            'domain' => $domain,
            'key_path' => $keyPath,
            'path_style' => $pathStyle,
        ], JSON_UNESCAPED_SLASHES),
        'enabled' => $enabled,
    ];
}

/** @param array<string,mixed> $body */
function nexrec_destination_save(array $body, string $username): array {
    $id = trim((string) ($body['id'] ?? ''));
    $existing = null;
    if ($id !== '') {
        if (preg_match('/^dst_[a-f0-9]{12}$/', $id) !== 1) {
            throw new InvalidArgumentException('invalid id');
        }
        $st = nexrec_db()->prepare('SELECT * FROM destinations WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $existing = nexrec_row($st->execute());
        if ($existing === null) {
            throw new InvalidArgumentException('destination not found');
        }
    } else {
        $id = nexrec_new_id('dst');
    }
    $norm = nexrec_destination_normalize($body, $existing);
    $now = nexrec_deliver_now();
    if ($existing === null) {
        $st = nexrec_db()->prepare(
            'INSERT INTO destinations (id,name,protocol,host,port,remote_prefix,username,secret_cipher,extra,enabled,created_by,created_at,updated_at)
             VALUES (:id,:name,:protocol,:host,:port,:prefix,:user,:cipher,:extra,:enabled,:by,:c,:u)'
        );
        $st->bindValue(':by', $username, SQLITE3_TEXT);
        $st->bindValue(':c', $now, SQLITE3_TEXT);
    } else {
        $st = nexrec_db()->prepare(
            'UPDATE destinations SET name=:name, protocol=:protocol, host=:host, port=:port, remote_prefix=:prefix,
             username=:user, secret_cipher=:cipher, extra=:extra, enabled=:enabled, updated_at=:u WHERE id=:id'
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
    $st->bindValue(':extra', $norm['extra'], SQLITE3_TEXT);
    $st->bindValue(':enabled', $norm['enabled'], SQLITE3_INTEGER);
    $st->bindValue(':u', $now, SQLITE3_TEXT);
    $st->execute();
    $got = nexrec_db()->prepare('SELECT * FROM destinations WHERE id=:id');
    $got->bindValue(':id', $id, SQLITE3_TEXT);
    $row = nexrec_row($got->execute());
    if ($row === null) {
        throw new RuntimeException('destination was not saved');
    }
    return nexrec_destination_public($row);
}

/** @return list<array<string,mixed>> */
function nexrec_destinations_public(bool $enabledOnly): array {
    $sql = 'SELECT * FROM destinations';
    if ($enabledOnly) {
        $sql .= ' WHERE enabled=1';
    }
    $sql .= ' ORDER BY name ASC';
    $res = nexrec_db()->query($sql);
    $out = [];
    while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
        $out[] = nexrec_destination_public($row);
    }
    return $out;
}

function nexrec_destination_delete(string $id): void {
    if (preg_match('/^dst_[a-f0-9]{12}$/', $id) !== 1) {
        throw new InvalidArgumentException('invalid id');
    }
    $busy = nexrec_db()->prepare(
        "SELECT id FROM deliveries WHERE destination_id=:id AND status IN ('queued','running') LIMIT 1"
    );
    $busy->bindValue(':id', $id, SQLITE3_TEXT);
    if (nexrec_row($busy->execute()) !== null) {
        throw new InvalidArgumentException('cancel the transfers for this destination first');
    }
    $delD = nexrec_db()->prepare('DELETE FROM deliveries WHERE destination_id=:id');
    $delD->bindValue(':id', $id, SQLITE3_TEXT);
    $delD->execute();
    $del = nexrec_db()->prepare('DELETE FROM destinations WHERE id=:id');
    $del->bindValue(':id', $id, SQLITE3_TEXT);
    $del->execute();
}

/**
 * @param list<mixed> $ids
 * @return list<string>
 */
function nexrec_deliver_ids(array $ids): array {
    $seen = [];
    $clean = [];
    foreach ($ids as $id) {
        $id = trim((string) $id);
        if ($id === '' || isset($seen[$id])) {
            continue;
        }
        $seen[$id] = true;
        if (preg_match('/^dst_[a-f0-9]{12}$/', $id) !== 1) {
            throw new InvalidArgumentException('unknown destination');
        }
        $clean[] = $id;
    }
    if (count($clean) > 20) {
        throw new InvalidArgumentException('too many destinations');
    }
    foreach ($clean as $id) {
        $st = nexrec_db()->prepare('SELECT id, enabled FROM destinations WHERE id=:id');
        $st->bindValue(':id', $id, SQLITE3_TEXT);
        $row = nexrec_row($st->execute());
        if ($row === null || (int) ($row['enabled'] ?? 0) !== 1) {
            throw new InvalidArgumentException('unknown destination');
        }
    }
    return $clean;
}

/** @param list<mixed> $ids */
function nexrec_deliver_attach(string $exportId, array $ids): void {
    $clean = nexrec_deliver_ids($ids);
    $now = nexrec_deliver_now();
    foreach ($clean as $id) {
        $ins = nexrec_db()->prepare(
            "INSERT INTO deliveries (id,export_id,destination_id,status,remote_path,error,created_at,cancel_requested)
             VALUES (:id,:export,:dest,'waiting','',NULL,:c,0)"
        );
        $ins->bindValue(':id', nexrec_new_id('dlv'), SQLITE3_TEXT);
        $ins->bindValue(':export', $exportId, SQLITE3_TEXT);
        $ins->bindValue(':dest', $id, SQLITE3_TEXT);
        $ins->bindValue(':c', $now, SQLITE3_TEXT);
        $ins->execute();
    }
}

/** True when a copy was still running and must stop before the re-encode. */
function nexrec_deliver_hold(string $exportId): bool {
    $busy = nexrec_db()->prepare(
        "SELECT id FROM deliveries WHERE export_id=:id AND status='running' LIMIT 1"
    );
    $busy->bindValue(':id', $exportId, SQLITE3_TEXT);
    $stopping = nexrec_row($busy->execute()) !== null;
    $stop = nexrec_db()->prepare(
        'UPDATE deliveries SET cancel_requested=1 WHERE export_id=:id AND status=\'running\''
    );
    $stop->bindValue(':id', $exportId, SQLITE3_TEXT);
    $stop->execute();
    $park = nexrec_db()->prepare(
        "UPDATE deliveries
         SET status='waiting', cancel_requested=0, error=NULL, queued_at=NULL,
             started_at=NULL, finished_at=NULL, progress_pct=NULL
         WHERE export_id=:id AND status IN ('waiting','queued','done','error','cancelled')"
    );
    $park->bindValue(':id', $exportId, SQLITE3_TEXT);
    $park->execute();
    return $stopping;
}

function nexrec_deliver_cancel_waiting(string $exportId): void {
    $st = nexrec_db()->prepare(
        "UPDATE deliveries SET status='cancelled', finished_at=:t, error=NULL, cancel_requested=0
         WHERE export_id=:id AND status='waiting'"
    );
    $st->bindValue(':t', nexrec_deliver_now(), SQLITE3_TEXT);
    $st->bindValue(':id', $exportId, SQLITE3_TEXT);
    $st->execute();
}

/** @return list<array<string,mixed>> */
function nexrec_deliveries_public(): array {
    $sql = "SELECT d.id, d.export_id, d.destination_id, d.status, d.remote_path, d.error,
                   d.created_at, d.queued_at, d.started_at, d.finished_at, d.progress_pct, d.cancel_requested,
                   dest.name AS destination_name, dest.protocol,
                   e.title AS export_title, e.status AS export_status
            FROM deliveries d
            LEFT JOIN destinations dest ON dest.id = d.destination_id
            LEFT JOIN exports e ON e.id = d.export_id
            ORDER BY CASE d.status
                       WHEN 'running' THEN 0
                       WHEN 'queued' THEN 1
                       WHEN 'waiting' THEN 2
                       WHEN 'error' THEN 3
                       ELSE 4
                     END,
                     d.created_at DESC
            LIMIT 200";
    $res = nexrec_db()->query($sql);
    $out = [];
    while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
        $out[] = [
            'id' => (string) ($row['id'] ?? ''),
            'export_id' => (string) ($row['export_id'] ?? ''),
            'destination_id' => (string) ($row['destination_id'] ?? ''),
            'destination_name' => (string) ($row['destination_name'] ?? ''),
            'protocol' => (string) ($row['protocol'] ?? ''),
            'export_title' => (string) ($row['export_title'] ?? ''),
            'export_status' => (string) ($row['export_status'] ?? ''),
            'status' => (string) ($row['status'] ?? ''),
            'remote_path' => (string) ($row['remote_path'] ?? ''),
            'error' => (string) ($row['error'] ?? ''),
            'created_at' => (string) ($row['created_at'] ?? ''),
            'queued_at' => (string) ($row['queued_at'] ?? ''),
            'started_at' => (string) ($row['started_at'] ?? ''),
            'finished_at' => (string) ($row['finished_at'] ?? ''),
            'progress_pct' => $row['progress_pct'] === null || $row['progress_pct'] === '' ? null : (float) $row['progress_pct'],
            'cancel_requested' => (int) ($row['cancel_requested'] ?? 0) === 1 ? 1 : 0,
        ];
    }
    return $out;
}

function nexrec_delivery_cancel(string $id): void {
    if (preg_match('/^dlv_[a-f0-9]{12}$/', $id) !== 1) {
        throw new InvalidArgumentException('invalid id');
    }
    $st = nexrec_db()->prepare('SELECT status FROM deliveries WHERE id=:id');
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    $row = nexrec_row($st->execute());
    if ($row === null) {
        throw new InvalidArgumentException('transfer not found');
    }
    $status = (string) ($row['status'] ?? '');
    if ($status === 'queued' || $status === 'waiting') {
        $up = nexrec_db()->prepare(
            "UPDATE deliveries SET status='cancelled', finished_at=:t, cancel_requested=0, error=NULL
             WHERE id=:id AND status IN ('queued','waiting')"
        );
        $up->bindValue(':t', nexrec_deliver_now(), SQLITE3_TEXT);
        $up->bindValue(':id', $id, SQLITE3_TEXT);
        $up->execute();
        return;
    }
    if ($status === 'running') {
        $up = nexrec_db()->prepare('UPDATE deliveries SET cancel_requested=1 WHERE id=:id AND status=\'running\'');
        $up->bindValue(':id', $id, SQLITE3_TEXT);
        $up->execute();
        return;
    }
    throw new InvalidArgumentException('that transfer is not in the queue');
}

function nexrec_delivery_retry(string $id): void {
    if (preg_match('/^dlv_[a-f0-9]{12}$/', $id) !== 1) {
        throw new InvalidArgumentException('invalid id');
    }
    $st = nexrec_db()->prepare(
        'SELECT d.status, e.status AS export_status
         FROM deliveries d LEFT JOIN exports e ON e.id = d.export_id WHERE d.id=:id'
    );
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    $row = nexrec_row($st->execute());
    if ($row === null) {
        throw new InvalidArgumentException('transfer not found');
    }
    if ((string) ($row['export_status'] ?? '') !== 'done') {
        throw new InvalidArgumentException('the export has not finished');
    }
    if (!in_array((string) ($row['status'] ?? ''), ['error', 'cancelled'], true)) {
        throw new InvalidArgumentException('that transfer cannot be retried');
    }
    $up = nexrec_db()->prepare(
        "UPDATE deliveries
         SET status='queued', queued_at=:t, cancel_requested=0, error=NULL,
             started_at=NULL, finished_at=NULL, progress_pct=NULL
         WHERE id=:id AND status IN ('error','cancelled')"
    );
    $up->bindValue(':t', nexrec_deliver_now(), SQLITE3_TEXT);
    $up->bindValue(':id', $id, SQLITE3_TEXT);
    $up->execute();
}

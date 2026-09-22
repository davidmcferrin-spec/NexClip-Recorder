<?php
/**
 * Local bcrypt + optional recorder-local LDAP + NexAPP session auth.
 * Production Nex* path is local users + NexAPP (SAML at the hub), not LDAP.
 * SQLite WAL. CLI-safe (no session start unless HTTP).
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-env.php';
require_once __DIR__ . '/nexrec-nexapp.php';

const NEXREC_ROLES = ['admin', 'operator', 'viewer'];

function nexrec_now_iso(): string {
    return gmdate('Y-m-d\TH:i:s\Z');
}

function nexrec_new_id(string $prefix): string {
    return $prefix . '_' . bin2hex(random_bytes(6));
}

function nexrec_db_path(): string {
    nexrec_load_station_env();
    $p = getenv('NEXREC_DB');
    if (is_string($p) && $p !== '') {
        return $p;
    }
    return nexrec_data_dir() . '/nexrec.db';
}

function nexrec_db(): SQLite3 {
    static $db = null;
    if ($db instanceof SQLite3) {
        return $db;
    }
    $path = nexrec_db_path();
    $dir = dirname($path);
    if (!is_dir($dir)) {
        mkdir($dir, 0770, true);
    }
    $db = new SQLite3($path);
    $db->busyTimeout(5000);
    $db->exec('PRAGMA foreign_keys = ON');
    $db->exec('PRAGMA journal_mode = WAL');
    return $db;
}

function nexrec_migrate(): void {
    $schema = dirname(__DIR__) . '/schema.sql';
    $sql = (string) file_get_contents($schema);
    nexrec_db()->exec($sql);
    nexrec_ensure_input_feature_columns();
    nexrec_ensure_fts();
}

function nexrec_ensure_input_feature_columns(): void {
    $want = [
        'feat_scte' => 'INTEGER NOT NULL DEFAULT 0',
        'feat_av_anomaly' => 'INTEGER NOT NULL DEFAULT 0',
        'feat_captions' => 'INTEGER NOT NULL DEFAULT 0',
        'feat_transcribe' => 'INTEGER NOT NULL DEFAULT 0',
        'feat_nielsen' => 'INTEGER NOT NULL DEFAULT 0',
        'feat_monitors' => 'INTEGER NOT NULL DEFAULT 0',
        'thresh_freeze_s' => 'REAL NOT NULL DEFAULT 2.0',
        'thresh_black_s' => 'REAL NOT NULL DEFAULT 2.0',
        'thresh_bars_s' => 'REAL NOT NULL DEFAULT 5.0',
        'transcribe_engine' => 'TEXT',
        'nexclip_slot' => 'INTEGER',
        'keep_interlace' => 'INTEGER',
    ];
    $have = [];
    $res = nexrec_db()->query('PRAGMA table_info(inputs)');
    while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
        $have[(string) $row['name']] = true;
    }
    foreach ($want as $name => $decl) {
        if (empty($have[$name])) {
            nexrec_db()->exec('ALTER TABLE inputs ADD COLUMN ' . $name . ' ' . $decl);
        }
    }
    $expHave = [];
    $res = nexrec_db()->query('PRAGMA table_info(exports)');
    while ($res !== false && ($row = $res->fetchArray(SQLITE3_ASSOC))) {
        $expHave[(string) $row['name']] = true;
    }
    if (empty($expHave['nexclip_capture_id'])) {
        nexrec_db()->exec('ALTER TABLE exports ADD COLUMN nexclip_capture_id TEXT');
    }
}

function nexrec_ensure_fts(): bool {
    return (bool) nexrec_db()->exec(
        'CREATE VIRTUAL TABLE IF NOT EXISTS captions_fts USING fts5(
          id UNINDEXED, input_id UNINDEXED, kind UNINDEXED, t_start UNINDEXED, speaker UNINDEXED, text
        )'
    );
}

function nexrec_row(SQLite3Result|false $res): ?array {
    if ($res === false) {
        return null;
    }
    $r = $res->fetchArray(SQLITE3_ASSOC);
    return $r === false ? null : $r;
}

function nexrec_session_start(): void {
    if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
        return;
    }
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    $dir = nexrec_data_dir() . '/sessions';
    if (!is_dir($dir)) {
        mkdir($dir, 0770, true);
    }
    session_name('NEXREC_AUTH');
    session_save_path($dir);
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'httponly' => true,
        'samesite' => 'Lax',
        'secure' => (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off'),
    ]);
    session_start();
}

function nexrec_session_clear(): void {
    nexrec_session_start();
    $_SESSION = [];
    if (session_status() === PHP_SESSION_ACTIVE) {
        session_destroy();
    }
}

function nexrec_user_public(array $row): array {
    return [
        'id' => $row['id'],
        'username' => $row['username'],
        'email' => $row['email'] ?? '',
        'display_name' => $row['display_name'] ?? $row['username'],
        'role' => $row['role'],
        'source' => $row['source'] ?? 'local',
        'must_change_password' => !empty($row['must_change_password']),
        'auth' => 'user',
    ];
}

function nexrec_user_find(string $username): ?array {
    $st = nexrec_db()->prepare('SELECT * FROM users WHERE username = :u COLLATE NOCASE');
    $st->bindValue(':u', $username, SQLITE3_TEXT);
    return nexrec_row($st->execute());
}

function nexrec_user_find_id(string $id): ?array {
    $st = nexrec_db()->prepare('SELECT * FROM users WHERE id = :i');
    $st->bindValue(':i', $id, SQLITE3_TEXT);
    return nexrec_row($st->execute());
}

function nexrec_user_create(array $in): array {
    $username = strtolower(trim((string) ($in['username'] ?? '')));
    if (!preg_match('/^[a-z0-9._-]{2,64}$/', $username)) {
        throw new InvalidArgumentException('invalid username');
    }
    $role = (string) ($in['role'] ?? 'viewer');
    if (!in_array($role, NEXREC_ROLES, true)) {
        throw new InvalidArgumentException('invalid role');
    }
    $password = (string) ($in['password'] ?? '');
    $hash = null;
    if ($password !== '') {
        if (strlen($password) < 8) {
            throw new InvalidArgumentException('password must be at least 8 characters');
        }
        $hash = password_hash($password, PASSWORD_BCRYPT);
    }
    $now = nexrec_now_iso();
    $id = $in['id'] ?? nexrec_new_id('usr');
    $st = nexrec_db()->prepare(
        'INSERT INTO users (id, username, password_hash, email, display_name, role, source, nexapp_sub, disabled_at, must_change_password, created_at, updated_at)
         VALUES (:id,:username,:hash,:email,:dn,:role,:source,:sub,NULL,:mcp,:c,:u)'
    );
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    $st->bindValue(':username', $username, SQLITE3_TEXT);
    $st->bindValue(':hash', $hash, SQLITE3_TEXT);
    $st->bindValue(':email', $in['email'] ?? null, SQLITE3_TEXT);
    $st->bindValue(':dn', $in['display_name'] ?? $username, SQLITE3_TEXT);
    $st->bindValue(':role', $role, SQLITE3_TEXT);
    $st->bindValue(':source', $in['source'] ?? 'local', SQLITE3_TEXT);
    $st->bindValue(':sub', $in['nexapp_sub'] ?? null, SQLITE3_TEXT);
    $st->bindValue(':mcp', !empty($in['must_change_password']) ? 1 : 0, SQLITE3_INTEGER);
    $st->bindValue(':c', $now, SQLITE3_TEXT);
    $st->bindValue(':u', $now, SQLITE3_TEXT);
    $st->execute();
    $row = nexrec_user_find_id($id);
    if ($row === null) {
        throw new RuntimeException('create failed');
    }
    return $row;
}

function nexrec_user_verify_local(string $username, string $password): ?array {
    $row = nexrec_user_find($username);
    if ($row === null || !empty($row['disabled_at'])) {
        return null;
    }
    if (($row['source'] ?? 'local') !== 'local') {
        return null;
    }
    $hash = (string) ($row['password_hash'] ?? '');
    if ($hash === '' || !password_verify($password, $hash)) {
        return null;
    }
    return $row;
}

function nexrec_ldap_enabled(): bool {
    nexrec_load_station_env();
    if (function_exists('nexrec_setting_bool')) {
        return nexrec_setting_bool('ldap.enabled');
    }
    $v = getenv('NEXREC_LDAP_ENABLED');
    return is_string($v) && in_array(strtolower($v), ['1', 'true', 'yes', 'on'], true);
}

function nexrec_user_verify_ldap(string $username, string $password): ?array {
    if (!nexrec_ldap_enabled()) {
        return null;
    }
    if (!function_exists('ldap_connect')) {
        return null;
    }
    $url = (string) getenv('NEXREC_LDAP_URL');
    $base = (string) getenv('NEXREC_LDAP_BASE_DN');
    $filterTpl = (string) (getenv('NEXREC_LDAP_USER_FILTER') ?: '(uid=%s)');
    if ($url === '' || $base === '' || $password === '') {
        return null;
    }
    $conn = @ldap_connect($url);
    if ($conn === false) {
        return null;
    }
    ldap_set_option($conn, LDAP_OPT_PROTOCOL_VERSION, 3);
    ldap_set_option($conn, LDAP_OPT_REFERRALS, 0);
    $bindDn = (string) (getenv('NEXREC_LDAP_BIND_DN') ?: '');
    $bindPw = (string) (getenv('NEXREC_LDAP_BIND_PASSWORD') ?: '');
    if ($bindDn !== '') {
        if (!@ldap_bind($conn, $bindDn, $bindPw)) {
            ldap_unbind($conn);
            return null;
        }
    }
    $esc = function_exists('ldap_escape')
        ? ldap_escape($username, '', LDAP_ESCAPE_FILTER)
        : str_replace(['\\', '*', '(', ')', "\x00"], ['\\5c', '\\2a', '\\28', '\\29', '\\00'], $username);
    $filter = sprintf($filterTpl, $esc);
    $emailAttr = (string) (getenv('NEXREC_LDAP_EMAIL_ATTR') ?: 'mail');
    $dnAttr = (string) (getenv('NEXREC_LDAP_DISPLAY_ATTR') ?: 'cn');
    $sr = @ldap_search($conn, $base, $filter, ['dn', $emailAttr, $dnAttr]);
    if ($sr === false) {
        ldap_unbind($conn);
        return null;
    }
    $entries = ldap_get_entries($conn, $sr);
    if ($entries === false || (int) ($entries['count'] ?? 0) < 1) {
        ldap_unbind($conn);
        return null;
    }
    $dn = $entries[0]['dn'] ?? '';
    if ($dn === '' || !@ldap_bind($conn, $dn, $password)) {
        ldap_unbind($conn);
        return null;
    }
    $email = $entries[0][strtolower($emailAttr)][0] ?? '';
    $display = $entries[0][strtolower($dnAttr)][0] ?? $username;
    ldap_unbind($conn);

    $role = 'operator';
    $adminGroup = (string) (getenv('NEXREC_LDAP_ADMIN_GROUP') ?: '');
    // Group membership check is a follow-up; default operator is intentional.

    $existing = nexrec_user_find($username);
    if ($existing === null) {
        return nexrec_user_create([
            'username' => $username,
            'email' => $email,
            'display_name' => $display,
            'role' => $role,
            'source' => 'ldap',
        ]);
    }
    return $existing;
}

function nexrec_login_user(array $row): void {
    nexrec_session_start();
    $_SESSION['user_id'] = $row['id'];
    $_SESSION['username'] = $row['username'];
    $_SESSION['role'] = $row['role'];
    $_SESSION['source'] = $row['source'] ?? 'local';
    $_SESSION['must_change_password'] = !empty($row['must_change_password']);
}

function nexrec_me_payload(): ?array {
    nexrec_session_start();
    $id = $_SESSION['user_id'] ?? null;
    if (!is_string($id) || $id === '') {
        return null;
    }
    $row = nexrec_user_find_id($id);
    if ($row === null || !empty($row['disabled_at'])) {
        return null;
    }
    return nexrec_user_public($row);
}

function nexrec_require_roles(array $roles): array {
    $me = nexrec_me_payload();
    if ($me === null) {
        throw new RuntimeException('unauthorized');
    }
    if ($roles !== [] && !in_array($me['role'], $roles, true)) {
        throw new RuntimeException('forbidden');
    }
    return $me;
}

function nexrec_bearer_api_ok(): bool {
    nexrec_load_station_env();
    $key = (string) (getenv('NEXREC_API_KEY') ?: '');
    if ($key === '') {
        return false;
    }
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (is_string($header) && preg_match('/^Bearer\s+(\S+)/i', $header, $m) === 1) {
        return hash_equals($key, $m[1]);
    }
    $h = $_SERVER['HTTP_X_NEXREC_KEY'] ?? $_SERVER['HTTP_X_NEXCLIP_KEY'] ?? '';
    return is_string($h) && $h !== '' && hash_equals($key, $h);
}

function nexrec_login_nexapp(array $access): array {
    $sub = (string) ($access['sub'] ?? $access['email'] ?? '');
    if ($sub === '') {
        throw new RuntimeException('unauthorized');
    }
    $username = strtolower(preg_replace('/[^a-z0-9._-]+/i', '.', $sub) ?: 'nexapp-user');
    $username = substr($username, 0, 64);
    $role = nexrec_nexapp_map_role($access['role'] ?? 'operator');
    $existing = nexrec_user_find($username);
    if ($existing === null) {
        $st = nexrec_db()->prepare('SELECT * FROM users WHERE nexapp_sub = :s');
        $st->bindValue(':s', $sub, SQLITE3_TEXT);
        $existing = nexrec_row($st->execute());
    }
    if ($existing === null) {
        $existing = nexrec_user_create([
            'username' => $username,
            'email' => $access['email'] ?? '',
            'display_name' => $access['name'] ?? $username,
            'role' => $role,
            'source' => 'nexapp',
            'nexapp_sub' => $sub,
        ]);
    }
    nexrec_login_user($existing);
    return $existing;
}

function nexrec_seed_admin(): void {
    nexrec_migrate();
    $n = (int) nexrec_db()->querySingle('SELECT COUNT(*) FROM users');
    if ($n > 0) {
        return;
    }
    nexrec_load_station_env();
    $user = (string) (getenv('NEXREC_ADMIN_USER') ?: 'admin');
    $pass = (string) (getenv('NEXREC_ADMIN_PASSWORD') ?: 'password');
    nexrec_user_create([
        'username' => $user,
        'password' => $pass,
        'role' => 'admin',
        'source' => 'local',
        'must_change_password' => ($pass === 'password'),
    ]);
}

function nexrec_users_list(): array {
    $out = [];
    $res = nexrec_db()->query('SELECT * FROM users ORDER BY username');
    if ($res === false) {
        return [];
    }
    while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
        $out[] = nexrec_user_public($row);
    }
    return $out;
}

function nexrec_user_update(string $id, array $body): array {
    $row = nexrec_user_find_id($id);
    if ($row === null) {
        throw new InvalidArgumentException('user not found');
    }
    $role = isset($body['role']) ? (string) $body['role'] : $row['role'];
    if (!in_array($role, NEXREC_ROLES, true)) {
        throw new InvalidArgumentException('invalid role');
    }
    $email = array_key_exists('email', $body) ? $body['email'] : $row['email'];
    $dn = array_key_exists('display_name', $body) ? $body['display_name'] : $row['display_name'];
    $disabled = !empty($body['disabled']) ? nexrec_now_iso() : null;
    if (array_key_exists('disabled', $body) && empty($body['disabled'])) {
        $disabled = null;
    } elseif (!array_key_exists('disabled', $body)) {
        $disabled = $row['disabled_at'];
    }
    $hash = $row['password_hash'];
    $mcp = (int) $row['must_change_password'];
    if (!empty($body['password'])) {
        if (strlen((string) $body['password']) < 8) {
            throw new InvalidArgumentException('password must be at least 8 characters');
        }
        $hash = password_hash((string) $body['password'], PASSWORD_BCRYPT);
        $mcp = 0;
    }
    $st = nexrec_db()->prepare(
        'UPDATE users SET email=:e, display_name=:d, role=:r, password_hash=:h,
         disabled_at=:x, must_change_password=:m, updated_at=:u WHERE id=:id'
    );
    $st->bindValue(':e', $email, SQLITE3_TEXT);
    $st->bindValue(':d', $dn, SQLITE3_TEXT);
    $st->bindValue(':r', $role, SQLITE3_TEXT);
    $st->bindValue(':h', $hash, SQLITE3_TEXT);
    $st->bindValue(':x', $disabled, SQLITE3_TEXT);
    $st->bindValue(':m', $mcp, SQLITE3_INTEGER);
    $st->bindValue(':u', nexrec_now_iso(), SQLITE3_TEXT);
    $st->bindValue(':id', $id, SQLITE3_TEXT);
    $st->execute();
    $fresh = nexrec_user_find_id($id);
    if ($fresh === null) {
        throw new RuntimeException('update failed');
    }
    return $fresh;
}

function nexrec_whep_url(string $path): string {
    $host = $_SERVER['HTTP_HOST'] ?? '127.0.0.1';
    $hostOnly = explode(':', $host)[0];
    if (function_exists('nexrec_setting')) {
        $port = nexrec_setting('preview.whep_port');
    } else {
        $envPort = getenv('NEXREC_WHEP_PORT');
        $port = is_string($envPort) && $envPort !== '' ? $envPort : '8889';
    }
    if ($port === '') {
        $port = '8889';
    }
    return 'https://' . $hostOnly . ':' . $port . '/' . rawurlencode($path) . '/whep';
}

require_once __DIR__ . '/nexrec-settings.php';

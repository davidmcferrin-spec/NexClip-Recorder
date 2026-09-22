<?php
/**
 * Local bcrypt + optional recorder-local LDAP + NexAPP session auth.
 * Production Nex* path is local users + NexAPP (SAML at the hub), not LDAP.
 * Local PostgreSQL. CLI-safe (no session start unless HTTP).
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

if (!defined('SQLITE3_ASSOC')) {
    define('SQLITE3_ASSOC', 1);
    define('SQLITE3_TEXT', 3);
    define('SQLITE3_INTEGER', 1);
    define('SQLITE3_NULL', 5);
}

function nexrec_adapt_sql(string $sql): string {
    $sql = preg_replace("/datetime\\(\\s*'now'\\s*\\)/i", "to_char(timezone('utc', clock_timestamp()), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')", $sql) ?? $sql;
    $sql = str_replace('IFNULL(', 'COALESCE(', $sql);
    $sql = preg_replace('/\\s+COLLATE\\s+NOCASE/i', '', $sql) ?? $sql;
    $sql = preg_replace(
        '/captions_fts\\s+MATCH\\s+(:[A-Za-z_][A-Za-z0-9_]*|\\?)/i',
        "tsv @@ plainto_tsquery('simple', $1)",
        $sql
    ) ?? $sql;
    if (preg_match("/sqlite_master\\s+WHERE\\s+type\\s*=\\s*'table'\\s+AND\\s+name\\s*=\\s*'([A-Za-z0-9_]+)'/i", $sql, $m)) {
        return "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = '" . $m[1] . "'";
    }
    if (preg_match('/PRAGMA\\s+table_info\\(([A-Za-z0-9_]+)\\)/i', $sql, $m)) {
        return "SELECT column_name AS name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = '" . $m[1] . "' ORDER BY ordinal_position";
    }
    return $sql;
}

function nexrec_split_sql(string $sql): array {
    $sql = preg_replace('/--.*$/m', '', $sql) ?? $sql;
    $parts = [];
    $buf = '';
    $in = false;
    $len = strlen($sql);
    for ($i = 0; $i < $len; $i++) {
        $c = $sql[$i];
        if ($c === "'") {
            if ($in && $i + 1 < $len && $sql[$i + 1] === "'") {
                $buf .= "''";
                $i++;
                continue;
            }
            $in = !$in;
        }
        if ($c === ';' && !$in) {
            $t = trim($buf);
            if ($t !== '') {
                $parts[] = $t;
            }
            $buf = '';
            continue;
        }
        $buf .= $c;
    }
    $t = trim($buf);
    if ($t !== '') {
        $parts[] = $t;
    }
    return $parts;
}

final class NexrecResult {
    /** @param list<array<string,mixed>> $rows */
    public function __construct(private array $rows, private int $i = 0) {
    }

    public function fetchArray(int $mode = SQLITE3_ASSOC): array|false {
        if ($this->i >= count($this->rows)) {
            return false;
        }
        return $this->rows[$this->i++];
    }
}

final class NexrecStmt {
    /** @var array<string,mixed> */
    private array $vals = [];

    public function __construct(private PDOStatement $st) {
    }

    public function bindValue(string $name, mixed $value, int $type = SQLITE3_TEXT): void {
        $this->vals[$name] = $value;
    }

    public function execute(): NexrecResult {
        $this->st->execute($this->vals);
        $rows = $this->st->fetchAll(PDO::FETCH_ASSOC);
        return new NexrecResult(is_array($rows) ? $rows : []);
    }
}

final class NexrecDb {
    public function __construct(private PDO $pdo) {
    }

    public function exec(string $sql): bool {
        foreach (nexrec_split_sql($sql) as $stmt) {
            $this->pdo->exec(nexrec_adapt_sql($stmt));
        }
        return true;
    }

    public function query(string $sql): NexrecResult {
        $st = $this->pdo->query(nexrec_adapt_sql($sql));
        if ($st === false) {
            return new NexrecResult([]);
        }
        $rows = $st->fetchAll(PDO::FETCH_ASSOC);
        return new NexrecResult(is_array($rows) ? $rows : []);
    }

    public function prepare(string $sql): NexrecStmt {
        $st = $this->pdo->prepare(nexrec_adapt_sql($sql));
        if ($st === false) {
            throw new RuntimeException('prepare failed');
        }
        return new NexrecStmt($st);
    }

    public function querySingle(string $sql): mixed {
        $row = $this->query($sql)->fetchArray();
        if ($row === false) {
            return null;
        }
        return array_values($row)[0] ?? null;
    }
}

function nexrec_pg_schema(): string {
    $explicit = getenv('NEXREC_PGSCHEMA');
    if (is_string($explicit) && $explicit !== '') {
        if (!preg_match('/^[A-Za-z_][A-Za-z0-9_]{0,62}$/', $explicit)) {
            throw new RuntimeException('invalid NEXREC_PGSCHEMA');
        }
        return $explicit;
    }
    $legacy = getenv('NEXREC_DB');
    $db = getenv('NEXREC_PGDATABASE');
    $db = is_string($db) ? $db : '';
    // A filesystem path is a test isolation key, not a database file.
    // A configured station database (anything other than the test DB) stays on public.
    if (
        is_string($legacy)
        && (str_contains($legacy, '/') || str_ends_with($legacy, '.db'))
        && ($db === '' || $db === 'nexrec_test')
    ) {
        return 't_' . substr(sha1($legacy), 0, 16);
    }
    return 'public';
}

function nexrec_db_path(): string {
    $cfg = nexrec_pg_config();
    return 'pgsql://' . $cfg['user'] . '@' . $cfg['host'] . ':' . $cfg['port'] . '/' . $cfg['db'] . ' schema=' . $cfg['schema'];
}

/** @return array{host:string,port:string,db:string,user:string,password:string,schema:string} */
function nexrec_pg_config(): array {
    $legacy = getenv('NEXREC_DB');
    $testKey = is_string($legacy) && (str_contains($legacy, '/') || str_ends_with($legacy, '.db'));
    if ($testKey && getenv('NEXREC_PGDATABASE') === false) {
        putenv('NEXREC_PGHOST=127.0.0.1');
        putenv('NEXREC_PGPORT=5432');
        putenv('NEXREC_PGDATABASE=nexrec_test');
        putenv('NEXREC_PGUSER=nexrec_test');
        putenv('NEXREC_PGPASSWORD=nexrec_test');
    }
    nexrec_load_station_env();
    $host = getenv('NEXREC_PGHOST');
    $port = getenv('NEXREC_PGPORT');
    $db = getenv('NEXREC_PGDATABASE');
    $user = getenv('NEXREC_PGUSER');
    $pass = getenv('NEXREC_PGPASSWORD');
    $host = is_string($host) && $host !== '' ? $host : '127.0.0.1';
    $port = is_string($port) && $port !== '' ? $port : '5432';
    $db = is_string($db) ? $db : '';
    $user = is_string($user) ? $user : '';
    $pass = is_string($pass) ? $pass : '';
    if ($db === '') {
        $db = 'nexrec_test';
        $user = $user !== '' ? $user : 'nexrec_test';
        $pass = $pass !== '' ? $pass : 'nexrec_test';
    } elseif ($user === '' || $pass === '') {
        throw new RuntimeException('PostgreSQL requires NEXREC_PGDATABASE, NEXREC_PGUSER, and NEXREC_PGPASSWORD');
    }
    return [
        'host' => $host,
        'port' => $port,
        'db' => $db,
        'user' => $user,
        'password' => $pass,
        'schema' => nexrec_pg_schema(),
    ];
}

function nexrec_db(): NexrecDb {
    static $db = null;
    if ($db instanceof NexrecDb) {
        return $db;
    }
    $cfg = nexrec_pg_config();
    $pdo = new PDO(
        'pgsql:host=' . $cfg['host'] . ';port=' . $cfg['port'] . ';dbname=' . $cfg['db'],
        $cfg['user'],
        $cfg['password'],
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
            PDO::ATTR_STRINGIFY_FETCHES => false,
        ]
    );
    $schema = $cfg['schema'];
    if ($schema !== 'public') {
        $pdo->exec('CREATE SCHEMA IF NOT EXISTS ' . $schema);
    }
    $pdo->exec('SET search_path TO ' . $schema);
    $db = new NexrecDb($pdo);
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
    $n = nexrec_db()->querySingle(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = 'captions_fts'"
    );
    return (int) $n === 1;
}

function nexrec_row(NexrecResult|false $res): ?array {
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
    $st = nexrec_db()->prepare('SELECT * FROM users WHERE lower(username) = lower(:u)');
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

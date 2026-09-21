<?php
/**
 * JSON auth API: login, logout, me, LDAP, NexAPP SSO, users, whep_jwt.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';

function nexrec_auth_fail(int $status, string $message): never {
    if (!headers_sent()) {
        header('Content-Type: application/json');
        header('Cache-Control: no-store');
    }
    http_response_code($status);
    echo json_encode(['ok' => false, 'error' => $message], JSON_UNESCAPED_SLASHES);
    exit;
}

function nexrec_auth_ok(array $extra = []): never {
    if (!headers_sent()) {
        header('Content-Type: application/json');
        header('Cache-Control: no-store');
    }
    echo json_encode(array_merge(['ok' => true], $extra), JSON_UNESCAPED_SLASHES);
    exit;
}

function nexrec_auth_body(): array {
    $raw = file_get_contents('php://input');
    if (!is_string($raw) || trim($raw) === '') {
        return [];
    }
    $j = json_decode($raw, true);
    return is_array($j) ? $j : [];
}

if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
    return;
}

nexrec_load_station_env();
try {
    nexrec_migrate();
    nexrec_seed_admin();
} catch (Throwable $e) {
    nexrec_auth_fail(500, 'auth store unavailable');
}

$body = nexrec_auth_body();
$action = $_GET['action'] ?? ($body['action'] ?? '');
$action = is_string($action) ? trim($action) : '';
if ($action === '') {
    nexrec_auth_fail(400, 'missing action');
}

try {
    if ($action === 'login') {
        $username = (string) ($body['username'] ?? '');
        $password = (string) ($body['password'] ?? '');
        $row = nexrec_user_verify_local($username, $password);
        if ($row === null) {
            $row = nexrec_user_verify_ldap($username, $password);
        }
        if ($row === null) {
            nexrec_auth_fail(401, 'invalid credentials');
        }
        nexrec_login_user($row);
        nexrec_auth_ok(['user' => nexrec_me_payload()]);
    }

    if ($action === 'nexapp_sso' || $action === 'portal_sso') {
        $jwt = trim((string) ($body['jwt'] ?? $body['ticket'] ?? $_GET['nexapp_ticket'] ?? ''));
        if ($jwt === '') {
            nexrec_auth_fail(400, 'jwt required');
        }
        $ex = nexrec_nexapp_exchange_ticket($jwt);
        $token = (string) ($ex['token'] ?? $jwt);
        $access = nexrec_nexapp_check_access($token);
        if (empty($access['ok'])) {
            nexrec_auth_fail((int) ($access['status'] ?? 401), (string) ($access['error'] ?? 'unauthorized'));
        }
        nexrec_login_nexapp($access);
        nexrec_auth_ok(['user' => nexrec_me_payload(), 'source' => 'nexapp']);
    }

    if ($action === 'nexapp_status') {
        $enabled = getenv('NEXREC_NEXAPP_ENABLED');
        $on = is_string($enabled) && in_array(strtolower($enabled), ['1', 'true', 'yes'], true);
        nexrec_auth_ok([
            'enabled' => $on,
            'login_url' => $on ? nexrec_nexapp_login_url('/login') : null,
            'instance_id' => getenv('NEXREC_INSTANCE_ID') ?: null,
        ]);
    }

    if ($action === 'logout') {
        nexrec_session_clear();
        nexrec_auth_ok();
    }

    if ($action === 'me') {
        $me = nexrec_me_payload();
        if ($me === null) {
            nexrec_auth_fail(401, 'unauthorized');
        }
        nexrec_auth_ok(['user' => $me, 'instance_id' => getenv('NEXREC_INSTANCE_ID') ?: null]);
    }

    if ($action === 'change_password') {
        $me = nexrec_require_roles([]);
        $row = nexrec_user_find_id($me['id']);
        if ($row === null) {
            nexrec_auth_fail(401, 'unauthorized');
        }
        $current = (string) ($body['current_password'] ?? '');
        $next = (string) ($body['new_password'] ?? '');
        if (($row['password_hash'] ?? '') === '' || !password_verify($current, (string) $row['password_hash'])) {
            nexrec_auth_fail(401, 'invalid credentials');
        }
        if (strlen($next) < 8) {
            nexrec_auth_fail(400, 'password must be at least 8 characters');
        }
        if ($next === 'password') {
            nexrec_auth_fail(400, 'choose a password other than the default');
        }
        nexrec_user_update($row['id'], ['password' => $next]);
        $fresh = nexrec_user_find_id($row['id']);
        nexrec_login_user($fresh ?? $row);
        nexrec_auth_ok(['user' => nexrec_me_payload()]);
    }

    if ($action === 'whep_jwt') {
        nexrec_require_roles([]);
        $path = strtolower(trim((string) ($body['path'] ?? $_GET['path'] ?? '')));
        if (!preg_match('/^in[0-9]$/', $path)) {
            nexrec_auth_fail(400, 'invalid path');
        }
        // v0: signed placeholder; MediaMTX JWT comes in a later pass (NexVUE JWKS).
        $jwt = 'local';
        nexrec_auth_ok([
            'jwt' => $jwt,
            'path' => $path,
            'whep_url' => nexrec_whep_url($path),
            'ice_servers' => [],
            'egress' => 'local',
        ]);
    }

    if ($action === 'users_list') {
        nexrec_require_roles(['admin']);
        nexrec_auth_ok(['users' => nexrec_users_list()]);
    }

    if ($action === 'user_create') {
        nexrec_require_roles(['admin']);
        try {
            $row = nexrec_user_create([
                'username' => (string) ($body['username'] ?? ''),
                'password' => (string) ($body['password'] ?? ''),
                'email' => $body['email'] ?? null,
                'role' => (string) ($body['role'] ?? 'viewer'),
                'must_change_password' => !empty($body['must_change_password']),
            ]);
        } catch (InvalidArgumentException $e) {
            nexrec_auth_fail(400, $e->getMessage());
        }
        nexrec_auth_ok(['user' => nexrec_user_public($row)]);
    }

    if ($action === 'user_update') {
        nexrec_require_roles(['admin']);
        $id = (string) ($body['id'] ?? '');
        if ($id === '') {
            nexrec_auth_fail(400, 'missing id');
        }
        try {
            $row = nexrec_user_update($id, $body);
        } catch (InvalidArgumentException $e) {
            nexrec_auth_fail(400, $e->getMessage());
        }
        nexrec_auth_ok(['user' => nexrec_user_public($row)]);
    }

    if ($action === 'api_ping') {
        if (!nexrec_bearer_api_ok()) {
            nexrec_auth_fail(401, 'unauthorized');
        }
        $ver = trim((string) @file_get_contents(dirname(__DIR__) . '/VERSION'));
        nexrec_auth_ok([
            'service' => 'nexclip-recorder',
            'version' => $ver !== '' ? $ver : null,
            'instance_id' => getenv('NEXREC_INSTANCE_ID') ?: null,
            'api' => 'v1',
        ]);
    }

    nexrec_auth_fail(400, 'unknown action');
} catch (RuntimeException $e) {
    $msg = $e->getMessage();
    if ($msg === 'unauthorized') {
        nexrec_auth_fail(401, $msg);
    }
    if ($msg === 'forbidden') {
        nexrec_auth_fail(403, $msg);
    }
    nexrec_auth_fail(500, $msg);
} catch (Throwable $e) {
    nexrec_auth_fail(500, 'internal error');
}

<?php
/**
 * NexAPP identity for NexCLIP Recorder.
 *
 * Same-host Alias: RS256 JWT + AccessService / GET access.php
 *   (NexAPP/examples/nexapp-access-client.php).
 * WAN (primary): launch.php ticket → POST /api/launch/redeem.php
 *   (NexAPP/examples/nexapp-launch-redeem.php). NexAPP never connects in.
 *
 * One unique NEXAPP_SERVICE_ID per host (NexAPP convention for Recorder
 * and standalone NexClip). Do not share an id across machines. Do not
 * invent an instance_id grant gate.
 */
declare(strict_types=1);

const NEXREC_NEXAPP_COOKIE = 'NexAPP_AUTH';

/** Example first-host id. Each machine must set NEXAPP_SERVICE_ID uniquely. */
function nexrec_nexapp_service_id(): string {
    $o = getenv('NEXAPP_SERVICE_ID');
    return (is_string($o) && $o !== '') ? $o : 'nexclip-recorder-ctl1';
}

function nexrec_nexapp_issuer(): string {
    $o = getenv('NEXAPP_ISSUER');
    return (is_string($o) && $o !== '') ? rtrim($o, '/') : '';
}

function nexrec_nexapp_public_key_path(): string {
    $o = getenv('NEXAPP_PUBLIC_KEY_PATH');
    if (is_string($o) && $o !== '') {
        return $o;
    }
    return '/var/www/nexapp/keys/jwt_public.pem';
}

function nexrec_nexapp_root(): string {
    $o = getenv('NEXAPP_ROOT');
    return (is_string($o) && $o !== '') ? rtrim($o, '/\\') : '/var/www/nexapp';
}

function nexrec_nexapp_access_url(): string {
    $o = getenv('NEXAPP_ACCESS_URL');
    if (is_string($o) && $o !== '') {
        return $o;
    }
    $iss = nexrec_nexapp_issuer();
    return ($iss !== '') ? ($iss . '/api/access.php') : '';
}

function nexrec_nexapp_redeem_url(): string {
    $o = getenv('NEXAPP_REDEEM_URL');
    if (is_string($o) && $o !== '') {
        return $o;
    }
    $iss = nexrec_nexapp_issuer();
    return ($iss !== '') ? ($iss . '/api/launch/redeem.php') : '';
}

function nexrec_nexapp_launch_secret(): string {
    $o = getenv('NEXAPP_LAUNCH_SECRET');
    return is_string($o) ? $o : '';
}

/** WAN launch URL. next must be a same-origin path. */
function nexrec_nexapp_sso_url(?string $next = null): string {
    $iss = nexrec_nexapp_issuer();
    $base = ($iss !== '') ? $iss : 'https://nexapp.example.internal';
    $url = $base . '/launch.php?service_id=' . rawurlencode(nexrec_nexapp_service_id());
    if (is_string($next) && str_starts_with($next, '/') && !str_starts_with($next, '//') && !str_contains($next, ':')) {
        $url .= '&next=' . rawurlencode($next);
    }
    return $url;
}

/** @deprecated use nexrec_nexapp_sso_url — Alias leftover login.php */
function nexrec_nexapp_login_url(?string $next = null): string {
    $explicit = getenv('NEXAPP_LOGIN_URL');
    if (is_string($explicit) && $explicit !== '' && !str_contains($explicit, 'login.php')) {
        // Operator pointed LOGIN_URL at launch.php already.
        return nexrec_nexapp_sso_url($next);
    }
    return nexrec_nexapp_sso_url($next);
}

function nexrec_nexapp_logout_url(): string {
    $o = getenv('NEXAPP_LOGOUT_URL');
    if (is_string($o) && $o !== '') {
        return $o;
    }
    $iss = nexrec_nexapp_issuer();
    return ($iss !== '') ? ($iss . '/logout.php') : 'https://nexapp.example.internal/logout.php';
}

function nexrec_nexapp_token_from_request(): ?string {
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    if (is_string($header) && preg_match('/^Bearer\s+(\S+)/i', $header, $m) === 1) {
        return $m[1];
    }
    $cookie = $_COOKIE[NEXREC_NEXAPP_COOKIE] ?? null;
    return is_string($cookie) && $cookie !== '' ? $cookie : null;
}

function nexrec_nexapp_ticket_from_request(): ?string {
    $q = $_GET['ticket'] ?? $_GET['nexapp_ticket'] ?? null;
    if (is_string($q) && $q !== '') {
        return $q;
    }
    return null;
}

function nexrec_b64url_decode(string $data): string {
    $remainder = strlen($data) % 4;
    if ($remainder) {
        $data .= str_repeat('=', 4 - $remainder);
    }
    $out = base64_decode(strtr($data, '-_', '+/'), true);
    if ($out === false) {
        throw new RuntimeException('bad b64');
    }
    return $out;
}

function nexrec_nexapp_verify_jwt(string $jwt): object {
    $pemPath = nexrec_nexapp_public_key_path();
    if (!is_readable($pemPath)) {
        throw new RuntimeException('JWT public key unreadable');
    }
    $parts = explode('.', $jwt);
    if (count($parts) !== 3) {
        throw new RuntimeException('Malformed JWT');
    }
    [$h64, $p64, $s64] = $parts;
    $header = json_decode(nexrec_b64url_decode($h64), false);
    $payload = json_decode(nexrec_b64url_decode($p64), false);
    if (!$header || !$payload || ($header->alg ?? '') !== 'RS256') {
        throw new RuntimeException('Bad JWT header/payload');
    }
    $pem = (string) file_get_contents($pemPath);
    $ok = openssl_verify("$h64.$p64", nexrec_b64url_decode($s64), $pem, OPENSSL_ALGO_SHA256);
    if ($ok !== 1) {
        throw new RuntimeException('Invalid signature');
    }
    if (!isset($payload->exp) || time() >= (int) $payload->exp) {
        throw new RuntimeException('Expired');
    }
    $issuer = nexrec_nexapp_issuer();
    if ($issuer !== '' && (string) ($payload->iss ?? '') !== $issuer) {
        throw new RuntimeException('Bad issuer');
    }
    return $payload;
}

/**
 * @return array{ok:bool,status:int,error?:string,sub?:string,email?:string,name?:string,role?:string,theme?:string,next?:string,source?:string}
 */
function nexrec_nexapp_check_access(?string $token = null): array {
    $stub = getenv('NEXREC_NEXAPP_ACCESS_STUB');
    if (is_string($stub) && $stub !== '' && is_readable($stub)) {
        $raw = json_decode((string) file_get_contents($stub), true);
        if (is_array($raw)) {
            $raw['ok'] = !empty($raw['ok']);
            $raw['status'] = (int) ($raw['status'] ?? ($raw['ok'] ? 200 : 401));
            return $raw;
        }
    }

    $token = $token ?? nexrec_nexapp_token_from_request();
    if ($token === null || $token === '') {
        return ['ok' => false, 'status' => 401, 'error' => 'unauthorized'];
    }

    try {
        nexrec_nexapp_verify_jwt($token);
    } catch (Throwable $e) {
        return ['ok' => false, 'status' => 401, 'error' => $e->getMessage() ?: 'unauthorized'];
    }

    $hubRoot = nexrec_nexapp_root();
    $bootstrap = $hubRoot . '/src/bootstrap.php';
    if (is_readable($bootstrap)) {
        require_once $bootstrap;
        $result = (new \NexApp\Auth\AccessService())->check($token, nexrec_nexapp_service_id());
        unset($result['user']);
        return $result;
    }

    $endpointBase = nexrec_nexapp_access_url();
    if ($endpointBase === '') {
        return ['ok' => false, 'status' => 503, 'error' => 'nexapp_unconfigured'];
    }
    $endpoint = $endpointBase . (str_contains($endpointBase, '?') ? '&' : '?')
        . 'service_id=' . rawurlencode(nexrec_nexapp_service_id());
    $ctx = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => "Authorization: Bearer {$token}\r\nAccept: application/json\r\n",
            'timeout' => 3,
            'ignore_errors' => true,
        ],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true],
    ]);
    $body = @file_get_contents($endpoint, false, $ctx);
    $status = 0;
    if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\b/', $http_response_header[0], $m) === 1) {
        $status = (int) $m[1];
    }
    if ($body === false || $status === 0) {
        return ['ok' => false, 'status' => 503, 'error' => 'introspect_failed'];
    }
    $data = json_decode($body, true);
    if (!is_array($data)) {
        return ['ok' => false, 'status' => 503, 'error' => 'introspect_failed'];
    }
    $data['status'] = $status;
    $data['ok'] = ($status === 200 && !empty($data['ok']));
    unset($data['user']);
    return $data;
}

/**
 * WAN launch redeem. Ticket is not a JWT.
 *
 * @return array{ok:bool,status:int,error?:string,sub?:string,email?:string,name?:string,role?:string,theme?:string,next?:string,source?:string}
 */
function nexrec_nexapp_redeem_launch(string $ticket): array {
    $stub = getenv('NEXREC_NEXAPP_TICKET_STUB');
    if (is_string($stub) && $stub !== '' && is_readable($stub)) {
        $raw = json_decode((string) file_get_contents($stub), true);
        if (!is_array($raw)) {
            return ['ok' => false, 'status' => 500, 'error' => 'redeem_stub_invalid'];
        }
        $raw['ok'] = !empty($raw['ok']);
        $raw['status'] = (int) ($raw['status'] ?? ($raw['ok'] ? 200 : 401));
        return $raw;
    }
    $url = nexrec_nexapp_redeem_url();
    $secret = nexrec_nexapp_launch_secret();
    if ($url === '' || $secret === '') {
        return ['ok' => false, 'status' => 503, 'error' => 'redeem_unconfigured'];
    }
    $payload = json_encode(['ticket' => $ticket, 'service_id' => nexrec_nexapp_service_id()]);
    $ctx = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n"
                . "Accept: application/json\r\n"
                . 'X-NexApp-Launch-Secret: ' . $secret . "\r\n",
            'content' => $payload ?: '{}',
            'timeout' => 5,
            'ignore_errors' => true,
        ],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true],
    ]);
    $body = @file_get_contents($url, false, $ctx);
    $status = 0;
    if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\b/', $http_response_header[0], $m) === 1) {
        $status = (int) $m[1];
    }
    if ($body === false || $status === 0) {
        return ['ok' => false, 'status' => 503, 'error' => 'redeem_failed'];
    }
    $data = json_decode($body, true);
    if (!is_array($data)) {
        return ['ok' => false, 'status' => 503, 'error' => 'redeem_failed'];
    }
    $data['status'] = $status;
    $data['ok'] = ($status === 200 && !empty($data['ok']));
    return $data;
}

/** True when the string looks like a three-part JWT. */
function nexrec_nexapp_looks_like_jwt(string $value): bool {
    return substr_count($value, '.') === 2;
}

function nexrec_nexapp_map_role(mixed $role): string {
    $r = is_string($role) ? strtolower($role) : '';
    if (in_array($r, ['admin', 'org_admin'], true)) {
        return 'admin';
    }
    if (in_array($r, ['viewer', 'org_viewer'], true)) {
        return 'viewer';
    }
    return 'operator';
}

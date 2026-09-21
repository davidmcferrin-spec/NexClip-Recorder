<?php
/**
 * NexAPP identity for NexCLIP Recorder (standalone WAN host or future Alias).
 *
 * Pattern copied from NexVUE web-portal/nexvue-portal-nexapp.php because the
 * NexAPP repo was not readable here. Verify against live NexAPP before prod.
 */
declare(strict_types=1);

const NEXREC_NEXAPP_COOKIE = 'NexAPP_AUTH';

function nexrec_nexapp_service_id(): string {
    $o = getenv('NEXAPP_SERVICE_ID');
    return (is_string($o) && $o !== '') ? $o : 'nexclip-recorder';
}

function nexrec_nexapp_issuer(): string {
    $o = getenv('NEXAPP_ISSUER');
    return (is_string($o) && $o !== '') ? $o : '';
}

function nexrec_nexapp_public_key_path(): string {
    $o = getenv('NEXAPP_PUBLIC_KEY_PATH');
    if (is_string($o) && $o !== '') {
        return $o;
    }
    return '/etc/nexrec/nexapp-jwt-public.pem';
}

function nexrec_nexapp_root(): string {
    $o = getenv('NEXAPP_ROOT');
    return (is_string($o) && $o !== '') ? rtrim($o, '/\\') : '/var/www/nexapp';
}

function nexrec_nexapp_login_url(?string $next = null): string {
    $o = getenv('NEXAPP_LOGIN_URL');
    $base = (is_string($o) && $o !== '') ? $o : 'https://nexapp.example.internal/login.php';
    $return = $next ?? '/';
    $join = str_contains($base, '?') ? '&' : '?';
    $instance = getenv('NEXREC_INSTANCE_ID') ?: '';
    return $base . $join . 'return=' . rawurlencode($return)
        . '&service_id=' . rawurlencode(nexrec_nexapp_service_id())
        . '&instance_id=' . rawurlencode((string) $instance);
}

function nexrec_nexapp_logout_url(): string {
    $o = getenv('NEXAPP_LOGOUT_URL');
    return (is_string($o) && $o !== '') ? $o : 'https://nexapp.example.internal/logout.php';
}

function nexrec_nexapp_token_from_request(): ?string {
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    if (is_string($header) && preg_match('/^Bearer\s+(\S+)/i', $header, $m) === 1) {
        return $m[1];
    }
    $q = $_GET['nexapp_ticket'] ?? $_GET['ticket'] ?? null;
    if (is_string($q) && $q !== '') {
        return $q;
    }
    $cookie = $_COOKIE[NEXREC_NEXAPP_COOKIE] ?? null;
    return is_string($cookie) && $cookie !== '' ? $cookie : null;
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
 * @return array{ok:bool,status:int,error?:string,sub?:string,email?:string,name?:string,role?:string,allowed_instances?:list<string>}
 */
function nexrec_nexapp_check_access(?string $token = null): array {
    $stub = getenv('NEXREC_NEXAPP_ACCESS_STUB');
    if (is_string($stub) && $stub !== '' && is_readable($stub)) {
        $raw = json_decode((string) file_get_contents($stub), true);
        if (is_array($raw)) {
            $raw['ok'] = !empty($raw['ok']);
            $raw['status'] = (int) ($raw['status'] ?? ($raw['ok'] ? 200 : 401));
            $instance = (string) (getenv('NEXREC_INSTANCE_ID') ?: '');
            return nexrec_nexapp_gate_instance($raw, $instance);
        }
    }

    $token = $token ?? nexrec_nexapp_token_from_request();
    if ($token === null || $token === '') {
        return ['ok' => false, 'status' => 401, 'error' => 'unauthorized'];
    }

    try {
        nexrec_nexapp_verify_jwt($token);
    } catch (Throwable $e) {
        return ['ok' => false, 'status' => 401, 'error' => 'unauthorized'];
    }

    $instance = (string) (getenv('NEXREC_INSTANCE_ID') ?: '');
    $hubRoot = nexrec_nexapp_root();
    $bootstrap = $hubRoot . '/src/bootstrap.php';
    if (is_readable($bootstrap)) {
        require_once $bootstrap;
        $svc = nexrec_nexapp_service_id();
        $result = (new \NexApp\Auth\AccessService())->check($token, $svc);
        unset($result['user']);
        $result = nexrec_nexapp_gate_instance($result, $instance);
        return $result;
    }

    $url = getenv('NEXAPP_ACCESS_URL');
    $endpointBase = (is_string($url) && $url !== '') ? $url : '';
    if ($endpointBase === '') {
        return ['ok' => false, 'status' => 503, 'error' => 'nexapp_unconfigured'];
    }
    $endpoint = $endpointBase . (str_contains($endpointBase, '?') ? '&' : '?')
        . 'service_id=' . rawurlencode(nexrec_nexapp_service_id())
        . '&instance_id=' . rawurlencode($instance);
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
    return nexrec_nexapp_gate_instance($data, $instance);
}

/**
 * WAN ticket exchange — path is an assumption (see docs/ASSUMPTIONS.md).
 *
 * @return array{ok:bool,status:int,token?:string,error?:string}
 */
function nexrec_nexapp_exchange_ticket(string $ticket): array {
    $stub = getenv('NEXREC_NEXAPP_TICKET_STUB');
    if (is_string($stub) && $stub !== '' && is_readable($stub)) {
        $raw = json_decode((string) file_get_contents($stub), true);
        return is_array($raw) ? $raw : ['ok' => false, 'status' => 500];
    }
    $url = getenv('NEXAPP_TICKET_URL');
    if (!is_string($url) || $url === '') {
        // Ticket may already be a JWT (NexVUE-style hash hop).
        return ['ok' => true, 'status' => 200, 'token' => $ticket];
    }
    $payload = json_encode([
        'ticket' => $ticket,
        'service_id' => nexrec_nexapp_service_id(),
        'instance_id' => getenv('NEXREC_INSTANCE_ID') ?: '',
    ]);
    $ctx = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\nAccept: application/json\r\n",
            'content' => $payload ?: '{}',
            'timeout' => 3,
            'ignore_errors' => true,
        ],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true],
    ]);
    $body = @file_get_contents($url, false, $ctx);
    $status = 0;
    if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\b/', $http_response_header[0], $m) === 1) {
        $status = (int) $m[1];
    }
    $data = is_string($body) ? json_decode($body, true) : null;
    if (!is_array($data)) {
        return ['ok' => false, 'status' => $status ?: 503, 'error' => 'ticket_exchange_failed'];
    }
    $data['status'] = $status;
    $data['ok'] = ($status === 200 && !empty($data['ok']));
    return $data;
}

function nexrec_nexapp_gate_instance(array $result, string $instance): array {
    $allowed = $result['allowed_instances'] ?? $result['instances'] ?? null;
    if (!is_array($allowed) || $allowed === [] || $instance === '') {
        return $result;
    }
    $ids = [];
    foreach ($allowed as $item) {
        if (is_string($item) && $item !== '') {
            $ids[] = $item;
        } elseif (is_array($item) && !empty($item['id'])) {
            $ids[] = (string) $item['id'];
        }
    }
    $result['allowed_instances'] = $ids;
    if ($ids !== [] && !in_array($instance, $ids, true)) {
        $result['ok'] = false;
        $result['status'] = 403;
        $result['error'] = 'instance_not_granted';
    }
    return $result;
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

<?php
// Source: NexAPP/examples/nexapp-access-client.php (local tree 2026-09-21).
// GitHub private; committed here as a pointer, not as a live dependency.


declare(strict_types=1);

/**
 * Copyable helper for PHP Alias apps on the NexApp VM.
 *
 * Always verify the JWT (or let AccessService do it). Then confirm live grants:
 *   1. Same VM (preferred): include hub bootstrap and use NexApp\Auth\AccessService
 *   2. Other stacks / isolated PHP: GET https://nexapp.nexstar.tv/api/access.php?service_id=...
 *
 * Do not skip signature verification. Do not take sub/role from the query string.
 * Do not give sibling apps the hub Postgres password as the primary design.
 */

const NEXAPP_COOKIE = 'NexAPP_AUTH';
const NEXAPP_ISSUER = 'https://nexapp.nexstar.tv';
const NEXAPP_PUBLIC_KEY = '/var/www/nexapp/keys/jwt_public.pem';
const NEXAPP_ROOT = '/var/www/nexapp';
const NEXAPP_ACCESS_URL = 'https://nexapp.nexstar.tv/api/access.php';

function nexapp_token_from_request(): ?string
{
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    if (is_string($header) && preg_match('/^Bearer\s+(\S+)/i', $header, $m) === 1) {
        return $m[1];
    }
    $cookie = $_COOKIE[NEXAPP_COOKIE] ?? null;
    return is_string($cookie) && $cookie !== '' ? $cookie : null;
}

function nexapp_b64url(string $data): string
{
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

/** RS256 + exp + iss. Throws on failure. Never accept a cookie because the PEM is missing. */
function nexapp_verify_jwt(string $jwt, string $pemPath = NEXAPP_PUBLIC_KEY, string $issuer = NEXAPP_ISSUER): object
{
    if (!is_readable($pemPath)) {
        throw new RuntimeException('JWT public key unreadable');
    }
    $parts = explode('.', $jwt);
    if (count($parts) !== 3) {
        throw new RuntimeException('Malformed JWT');
    }
    [$h64, $p64, $s64] = $parts;
    $header = json_decode(nexapp_b64url($h64), false);
    $payload = json_decode(nexapp_b64url($p64), false);
    if (!$header || !$payload || ($header->alg ?? '') !== 'RS256') {
        throw new RuntimeException('Bad JWT header/payload');
    }
    $pem = (string) file_get_contents($pemPath);
    $ok = openssl_verify("$h64.$p64", nexapp_b64url($s64), $pem, OPENSSL_ALGO_SHA256);
    if ($ok !== 1) {
        throw new RuntimeException('Invalid signature');
    }
    if (!isset($payload->exp) || time() >= (int) $payload->exp) {
        throw new RuntimeException('Expired');
    }
    if ((string) ($payload->iss ?? '') !== $issuer) {
        throw new RuntimeException('Bad issuer');
    }
    return $payload;
}

/**
 * Stale snapshot from the cookie. Prefer nexapp_check_access() for grants.
 * Missing/unknown role → user, never admin. Missing key → not assigned (null).
 */
function nexapp_catalog_role_from_claims(object $claims, string $serviceId): ?string
{
    $apps = $claims->apps ?? null;
    if (is_object($apps)) {
        $apps = get_object_vars($apps);
    }
    if (!is_array($apps)) {
        return null;
    }
    if ($apps !== [] && array_is_list($apps)) {
        return in_array($serviceId, array_map('strval', $apps), true) ? 'user' : null;
    }
    if (!array_key_exists($serviceId, $apps)) {
        return null;
    }
    return ($apps[$serviceId] === 'admin') ? 'admin' : 'user';
}

/**
 * Live grant check. JWT is verified first (locally or inside AccessService).
 *
 * @return array{ok: bool, status: int, error?: string, sub?: string, email?: string, name?: string, service_id?: string, role?: string, source?: string}
 */
function nexapp_check_access(string $serviceId, string $token, string $hubRoot = NEXAPP_ROOT): array
{
    $bootstrap = rtrim($hubRoot, '/\\') . '/src/bootstrap.php';
    if (is_readable($bootstrap)) {
        require_once $bootstrap;
        $result = (new \NexApp\Auth\AccessService())->check($token, $serviceId);
        unset($result['user']);
        return $result;
    }
    return nexapp_introspect($serviceId, $token);
}

/**
 * HTTP introspection. The hub still verifies the JWT — this is not an unsigned identity API.
 *
 * @return array{ok: bool, status: int, error?: string, sub?: string, email?: string, name?: string, service_id?: string, role?: string, source?: string}
 */
function nexapp_introspect(string $serviceId, string $token, string $url = NEXAPP_ACCESS_URL): array
{
    $endpoint = $url . (str_contains($url, '?') ? '&' : '?') . 'service_id=' . rawurlencode($serviceId);
    $ctx = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => "Authorization: Bearer {$token}\r\nAccept: application/json\r\n",
            'timeout' => 3,
            'ignore_errors' => true,
        ],
        'ssl' => [
            'verify_peer' => true,
            'verify_peer_name' => true,
        ],
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

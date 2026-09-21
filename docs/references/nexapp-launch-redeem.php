<?php
// Source: NexAPP/examples/nexapp-launch-redeem.php (local tree 2026-09-21).
// GitHub private; committed here as a pointer, not as a live dependency.


declare(strict_types=1);

/**
 * Copy into a WAN/VPN app (NexNOC and later). That app calls NexApp; NexApp never calls it.
 *
 * No session:
 *   header('Location: ' . nexapp_sso_url('nexnoc', $_SERVER['REQUEST_URI'] ?? '/'));
 *
 * Ticket on the SSO landing URL:
 *   $access = nexapp_redeem_launch(nexapp_ticket_from_request(), 'nexnoc');
 *   // $access: sub, email, name, role (user|admin), optional next, theme (system|light|dark)
 */

const NEXAPP_BASE = 'https://nexapp.nexstar.tv';
const NEXAPP_REDEEM_URL = NEXAPP_BASE . '/api/launch/redeem.php';
const NEXAPP_LAUNCH_SECRET = 'CHANGE_ME'; // config.php launch.redeem_secrets.<service_id>

function nexapp_sso_url(string $serviceId, ?string $next = null): string
{
    $url = NEXAPP_BASE . '/launch.php?service_id=' . rawurlencode($serviceId);
    if (is_string($next) && str_starts_with($next, '/') && !str_starts_with($next, '//') && !str_contains($next, ':')) {
        $url .= '&next=' . rawurlencode($next);
    }
    return $url;
}

/**
 * @return array{ok: bool, status: int, error?: string, sub?: string, email?: string, name?: string, service_id?: string, role?: string, source?: string, next?: string, theme?: string}
 */
function nexapp_redeem_launch(string $ticket, string $serviceId, string $url = NEXAPP_REDEEM_URL, string $secret = NEXAPP_LAUNCH_SECRET): array
{
    $payload = json_encode(['ticket' => $ticket, 'service_id' => $serviceId], JSON_THROW_ON_ERROR);
    $ctx = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n"
                . "Accept: application/json\r\n"
                . "X-NexApp-Launch-Secret: {$secret}\r\n",
            'content' => $payload,
            'timeout' => 5,
            'ignore_errors' => true,
        ],
        'ssl' => [
            'verify_peer' => true,
            'verify_peer_name' => true,
        ],
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

function nexapp_ticket_from_request(): ?string
{
    $q = $_GET['ticket'] ?? null;
    if (is_string($q) && $q !== '') {
        return $q;
    }
    return null;
}

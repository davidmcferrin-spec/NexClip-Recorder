<?php
/**
 * NexClip does not push into the recorder (Mode 2 node polls out).
 * This endpoint stays as a 410 marker so old invented webhooks fail loudly.
 */
declare(strict_types=1);

require_once __DIR__ . '/nexrec-auth-lib.php';

if (PHP_SAPI === 'cli' && getenv('NEXREC_AUTH_HTTP') === false) {
    return;
}

header('Content-Type: application/json');
http_response_code(410);
echo json_encode([
    'ok' => false,
    'error' => 'gone',
    'hint' => 'NexClip Mode 2 is node-initiated. See docs/NEXCLIP-HOOKS.md (register/checkin/export-requests/next).',
], JSON_UNESCAPED_SLASHES);
exit;

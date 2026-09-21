<?php
/**
 * Path front door for NexCLIP Recorder UI + /api/*.
 */
declare(strict_types=1);

function nexrec_app_root(): string {
    static $root = null;
    if ($root !== null) {
        return $root;
    }
    $env = getenv('NEXREC_APP_ROOT');
    if (is_string($env) && $env !== '') {
        $root = rtrim($env, '/\\');
        return $root;
    }
    $here = __DIR__;
    if (is_file($here . '/nexrec-auth.php')) {
        $root = $here;
        return $root;
    }
    $root = dirname($here);
    return $root;
}

function nexrec_web_request_path(): string {
    $uri = $_SERVER['REQUEST_URI'] ?? '/';
    $path = parse_url($uri, PHP_URL_PATH);
    if (!is_string($path) || $path === '') {
        $path = '/';
    }
    if ($path !== '/' && str_ends_with($path, '/')) {
        $path = rtrim($path, '/');
    }
    return $path;
}

/** @return never */
function nexrec_web_redirect(string $to, int $code = 302): void {
    if (!str_starts_with($to, 'http') && !str_starts_with($to, '/')) {
        $to = '/' . $to;
    }
    header('Location: ' . $to, true, $code);
    exit;
}

function nexrec_web_pages(): array {
    return [
        '/' => ['file' => 'live.html', 'roles' => null, 'public' => false],
        '/live' => ['file' => 'live.html', 'roles' => null, 'public' => false],
        '/export' => ['file' => 'export.html', 'roles' => null, 'public' => false],
        '/inputs' => ['file' => 'inputs.html', 'roles' => ['admin', 'operator'], 'public' => false],
        '/settings' => ['file' => 'settings.html', 'roles' => ['admin', 'operator'], 'public' => false],
        '/users' => ['file' => 'users.html', 'roles' => ['admin'], 'public' => false],
        '/services' => ['file' => 'services.html', 'roles' => ['admin'], 'public' => false],
        '/login' => ['file' => 'login.html', 'roles' => null, 'public' => true],
    ];
}

function nexrec_web_apis(): array {
    return [
        '/api/auth' => 'nexrec-auth.php',
        '/api/recorder' => 'nexrec-api.php',
        '/api/ops' => 'nexrec-ops.php',
        '/api/version' => 'nexrec-version.php',
        '/api/nexclip' => 'nexrec-nexclip.php',
    ];
}

function nexrec_web_query_suffix(): string {
    $q = $_SERVER['QUERY_STRING'] ?? '';
    return ($q !== '') ? ('?' . $q) : '';
}

function nexrec_web_authorize_page(array $page, string $path): void {
    if ($page['public']) {
        return;
    }
    require_once nexrec_app_root() . '/nexrec-auth-lib.php';
    try {
        nexrec_migrate();
        nexrec_seed_admin();
    } catch (Throwable $e) {
        nexrec_web_redirect('/login?next=' . rawurlencode($path . nexrec_web_query_suffix()));
    }
    $me = nexrec_me_payload();
    if ($me === null) {
        nexrec_web_redirect('/login?next=' . rawurlencode($path . nexrec_web_query_suffix()));
    }
    if (!empty($me['must_change_password'])) {
        nexrec_web_redirect('/login?change=1&next=' . rawurlencode($path . nexrec_web_query_suffix()));
    }
    $roles = $page['roles'];
    if (is_array($roles) && $roles !== []) {
        if (!in_array((string) ($me['role'] ?? ''), $roles, true)) {
            nexrec_web_redirect('/live');
        }
    }
}

function nexrec_web_serve_page(string $file): void {
    $path = nexrec_app_root() . '/pages/' . $file;
    if (!is_file($path)) {
        http_response_code(500);
        header('Content-Type: text/plain; charset=utf-8');
        echo "page missing: {$file}\n";
        exit;
    }
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    readfile($path);
    exit;
}

function nexrec_web_dispatch(): void {
    $path = nexrec_web_request_path();
    if (str_starts_with($path, '/assets/')) {
        http_response_code(404);
        header('Content-Type: text/plain; charset=utf-8');
        echo "not found\n";
        exit;
    }
    $apis = nexrec_web_apis();
    if (isset($apis[$path])) {
        $full = nexrec_app_root() . '/' . $apis[$path];
        require $full;
        exit;
    }
    if (preg_match('#^/api/exports/([A-Za-z0-9_]+)/file$#', $path, $m)) {
        $_GET['action'] = 'export_file';
        $_GET['id'] = $m[1];
        require nexrec_app_root() . '/nexrec-api.php';
        exit;
    }
    if (preg_match('#^/api/chunks/([A-Za-z0-9_]+)/media$#', $path, $m)) {
        $_GET['action'] = 'chunk_media';
        $_GET['id'] = $m[1];
        require nexrec_app_root() . '/nexrec-api.php';
        exit;
    }
    $pages = nexrec_web_pages();
    if (isset($pages[$path])) {
        $page = $pages[$path];
        nexrec_web_authorize_page($page, $path);
        nexrec_web_serve_page($page['file']);
    }
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo "not found\n";
    exit;
}

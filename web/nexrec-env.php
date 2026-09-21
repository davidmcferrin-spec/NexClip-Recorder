<?php
/**
 * Load KEY=value env files (bash-style quotes). Never eval.
 */
declare(strict_types=1);

function nexrec_read_env_file(string $path): array {
    if (!is_file($path) or !is_readable($path)) {
        return [];
    }
    $out = [];
    $lines = file($path, FILE_IGNORE_NEW_LINES);
    if ($lines === false) {
        return [];
    }
    foreach ($lines as $line) {
        $line = trim($line);
        if ($line === '' || str_starts_with($line, '#')) {
            continue;
        }
        if (str_starts_with($line, 'export ')) {
            $line = trim(substr($line, 7));
        }
        $eq = strpos($line, '=');
        if ($eq === false) {
            continue;
        }
        $key = trim(substr($line, 0, $eq));
        $val = trim(substr($line, $eq + 1));
        $len = strlen($val);
        if ($len >= 2 && ($val[0] === '"' || $val[0] === "'") && $val[$len - 1] === $val[0]) {
            $val = substr($val, 1, -1);
        }
        $out[$key] = $val;
    }
    return $out;
}

function nexrec_env_get(string $key, string $default = ''): string {
    $v = getenv($key);
    if (is_string($v) && $v !== '') {
        return $v;
    }
    return $default;
}

function nexrec_data_dir(): string {
    $d = nexrec_env_get('NEXREC_DATA_DIR');
    if ($d !== '') {
        return rtrim($d, '/');
    }
    $override = nexrec_env_get('NEXREC_DEMO_DATA');
    if ($override !== '') {
        return rtrim($override, '/');
    }
    return '/var/lib/nexrec';
}

function nexrec_load_station_env(): void {
    static $loaded = false;
    if ($loaded) {
        return;
    }
    $loaded = true;
    $candidates = [
        nexrec_env_get('NEXREC_ENV_FILE'),
        '/etc/nexrec/nexrec.env',
        dirname(__DIR__) . '/nexrec.env',
        dirname(__DIR__) . '/nexrec-example.env',
    ];
    foreach ($candidates as $path) {
        if ($path === '' || !is_readable($path)) {
            continue;
        }
        foreach (nexrec_read_env_file($path) as $k => $v) {
            if (getenv($k) === false) {
                putenv("{$k}={$v}");
                $_ENV[$k] = $v;
            }
        }
        break;
    }
}

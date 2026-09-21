<?php
/**
 * Public version stamp for the top-nav badge.
 */
declare(strict_types=1);

header('Content-Type: application/json');
header('Cache-Control: no-store');

function nexrec_read_version(string $path): string {
    if (!is_file($path) || !is_readable($path)) {
        return '';
    }
    $raw = trim((string) file_get_contents($path));
    if ($raw === '' || !preg_match('/^\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$/', $raw)) {
        return '';
    }
    return $raw;
}

$version = nexrec_read_version(dirname(__DIR__) . '/VERSION');
if ($version === '') {
    $version = nexrec_read_version('/usr/local/share/nexrec/VERSION');
}
if ($version === '') {
    $version = '0.0.0';
}

echo json_encode([
    'ok' => true,
    'version' => $version,
    'git_sha' => '',
    'git_branch' => '',
], JSON_UNESCAPED_SLASHES);

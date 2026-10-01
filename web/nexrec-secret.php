<?php
/**
 * Destination password cipher. Matches worker/nexrec_secret.py.
 * HMAC-SHA256 counter mode, then HMAC-SHA256 over nonce || ciphertext.
 * The key is NEXREC_DEST_KEY and is never written to the database.
 */
declare(strict_types=1);

function nexrec_dest_key(): string {
    $k = getenv('NEXREC_DEST_KEY');
    return is_string($k) ? $k : '';
}

/** @return array{0:string,1:string} */
function nexrec_secret_keys(string $master): array {
    $raw = hash('sha256', $master, true);
    $enc = hash_hmac('sha256', 'nexrec-dest-enc', $raw, true);
    $mac = hash_hmac('sha256', 'nexrec-dest-mac', $raw, true);
    return [$enc, $mac];
}

function nexrec_secret_xor(string $encKey, string $nonce, string $data): string {
    $out = '';
    $n = strlen($data);
    $offset = 0;
    $counter = 0;
    while ($offset < $n) {
        $block = hash_hmac('sha256', $nonce . pack('N', $counter), $encKey, true);
        $counter++;
        $take = substr($data, $offset, 32);
        $len = strlen($take);
        for ($i = 0; $i < $len; $i++) {
            $out .= chr(ord($take[$i]) ^ ord($block[$i]));
        }
        $offset += 32;
    }
    return $out;
}

function nexrec_secret_encrypt(string $plain, string $master, ?string $nonce = null): string {
    if ($master === '') {
        throw new InvalidArgumentException('NEXREC_DEST_KEY is not set');
    }
    [$enc, $mac] = nexrec_secret_keys($master);
    $nonceBin = $nonce === null ? random_bytes(16) : $nonce;
    if (strlen($nonceBin) !== 16) {
        throw new InvalidArgumentException('nonce must be 16 bytes');
    }
    $ct = nexrec_secret_xor($enc, $nonceBin, $plain);
    $tag = hash_hmac('sha256', $nonceBin . $ct, $mac, true);
    return base64_encode($nonceBin . $tag . $ct);
}

function nexrec_secret_decrypt(string $blob, string $master): string {
    if ($master === '') {
        throw new RuntimeException('NEXREC_DEST_KEY is not set');
    }
    $raw = base64_decode($blob, true);
    if ($raw === false || strlen($raw) < 48) {
        throw new RuntimeException('bad secret');
    }
    $nonce = substr($raw, 0, 16);
    $tag = substr($raw, 16, 32);
    $ct = substr($raw, 48);
    [$enc, $mac] = nexrec_secret_keys($master);
    $expect = hash_hmac('sha256', $nonce . $ct, $mac, true);
    if (!hash_equals($expect, $tag)) {
        throw new RuntimeException('bad secret');
    }
    return nexrec_secret_xor($enc, $nonce, $ct);
}

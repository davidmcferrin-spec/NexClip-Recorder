#!/usr/bin/env python3
"""Encrypt destination passwords for the database.

HMAC-SHA256 in counter mode, then HMAC-SHA256 over the nonce and ciphertext.
The same construction lives in web/nexrec-secret.php. The key is
NEXREC_DEST_KEY from the environment, not a column.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def dest_key(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return str(source.get("NEXREC_DEST_KEY") or "")


def _keys(master: str) -> tuple[bytes, bytes]:
    raw = hashlib.sha256(master.encode("utf-8")).digest()
    enc = hmac.new(raw, b"nexrec-dest-enc", hashlib.sha256).digest()
    mac = hmac.new(raw, b"nexrec-dest-mac", hashlib.sha256).digest()
    return enc, mac


def _xor(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    offset = 0
    counter = 0
    while offset < len(data):
        block = hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
        take = data[offset : offset + 32]
        out.extend(a ^ b for a, b in zip(take, block))
        offset += 32
    return bytes(out)


def encrypt_secret(plain: str, master: str, nonce: bytes | None = None) -> str:
    if master == "":
        raise RuntimeError("NEXREC_DEST_KEY is not set")
    enc, mac = _keys(master)
    nonce_b = os.urandom(16) if nonce is None else nonce
    if len(nonce_b) != 16:
        raise RuntimeError("nonce must be 16 bytes")
    ct = _xor(enc, nonce_b, plain.encode("utf-8"))
    tag = hmac.new(mac, nonce_b + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce_b + tag + ct).decode("ascii")


def decrypt_secret(blob: str, master: str) -> str:
    if master == "":
        raise RuntimeError("NEXREC_DEST_KEY is not set")
    try:
        raw = base64.b64decode(blob, validate=True)
    except Exception as exc:  # noqa: BLE001 — bad stored cipher
        raise RuntimeError("bad secret") from exc
    if len(raw) < 48:
        raise RuntimeError("bad secret")
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    enc, mac = _keys(master)
    expect = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, tag):
        raise RuntimeError("bad secret")
    return _xor(enc, nonce, ct).decode("utf-8")

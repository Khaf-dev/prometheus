"""
Auth core — API key generation, hashing, dan verification.

Design decisions:
- API key di-hash sebelum disimpan ke DB (bcrypt) — kalau DB bocor, key tidak exposed
- Format key: "prom_<32-char-random>" — prefix memudahkan identify + secret scanning
- Hanya satu active key untuk single-user (bisa di-rotate kapan saja)
- Timing-safe comparison untuk prevent timing attacks
"""


import hashlib
import hmac
import os
import secrets

import bcrypt

API_KEY_PREFIX = "prom_"
API_KEY_RANDOM_BYTES = 32


def generate_api_key() -> str:
    """
    Generate API key baru yang cryptographically secure.
    Format: prom_<64-char-hex>
    Contoh: prom_a3f8c2e1d4b7...

    PENTING: Key yang di-return ini hanya tampil SEKALI.
    Setelah di-hash dan disimpan, tidak bisa di-recover.
    """
    random_part = secrets.token_hex(API_KEY_RANDOM_BYTES)
    return f"{API_KEY_PREFIX}{random_part}"


def hash_api_key(api_key: str) -> str:
    """
    Hash API key dengan bcrypt untuk disimpan ke DB.
    bcrypt karena: slow by design (brute-force resistant), built-in salt.

    Trade-off: bcrypt lebih lambat dari SHA256, tapi untuk auth request
    yang low-frequency (bukan per-pixel), ini acceptable.
    """
    key_bytes = api_key.encode("utf-8")
    hashed = bcrypt.hashpw(key_bytes, bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """
    Verify API key dengan timing-safe comparison.
    bcrypt.checkpw sudah timing-safe by design.
    """
    try:
        return bcrypt.checkpw(
            plain_key.encode("utf-8"),
            hashed_key.encode("utf-8"),
        )
    except Exception:
        return False


def generate_key_id() -> str:
    """
    Short identifier untuk display (bukan secret).
    Dipakai untuk "Key ID: prom_a3f8...c2e1" di UI.
    Ini aman ditampilkan — bukan key-nya, hanya prefix hint.
    """
    return secrets.token_hex(4)
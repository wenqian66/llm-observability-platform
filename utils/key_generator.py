"""
utils/key_generator.py
Secure API key generation, hashing, and verification utilities

Key lifecycle:
  1. generate_api_key()        → plaintext dsrs-<32 base62> returned to caller ONCE
  2. hash_api_key(plaintext)   → bcrypt hash stored in DB — plaintext never persisted
  3. verify_api_key(plain, hash) → bcrypt.verify on every auth attempt (cache softens cost)
  4. cache_key(plaintext)      → sha256 digest used as in-memory cache lookup key
                                  so plaintext never sits in the cache dict itself
"""

import hashlib
import re
import secrets
import string

import bcrypt

# Base62 alphabet: 0-9, A-Z, a-z
# URL-safe and ~190 bits of entropy at 32 chars
_BASE62 = string.ascii_letters + string.digits
_KEY_PREFIX = "dsrs-"
_KEY_RANDOM_LENGTH = 32

# bcrypt work factor — 12 is the current industry baseline.
# Increase to 13-14 on hardware that can afford ~400ms per hash.
_BCRYPT_ROUNDS = 12


def generate_api_key() -> str:
    """
    Generate a secure API key.

    Format:  dsrs-<32 base62 chars>
    Example: dsrs-aB3xK9mNpQ2rS7tU1vW4yZ6cD8eF0gH
    Entropy: ~190 bits (log2(62^32)) — stronger than UUID v4's 128 bits

    Uses secrets.choice() which pulls from the OS CSPRNG (urandom/getrandom).
    The caller receives this value ONCE and must treat it as a secret.
    Only the bcrypt hash is ever stored in the database.
    """
    random_part = "".join(secrets.choice(_BASE62) for _ in range(_KEY_RANDOM_LENGTH))
    return f"{_KEY_PREFIX}{random_part}"


def hash_api_key(plaintext: str) -> str:
    """
    Produce a bcrypt hash of the plaintext API key for database storage.

    bcrypt is intentionally slow (work factor 12 ≈ 100–200ms) to resist
    offline brute-force attacks if the DB is ever compromised.

    Args:
        plaintext: The dsrs- prefixed key returned by generate_api_key()

    Returns:
        bcrypt hash string suitable for VARCHAR(255) storage
    """
    return bcrypt.hashpw(
        plaintext.encode(), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    ).decode()


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    """
    Verify a submitted API key against its stored bcrypt hash.

    Args:
        plaintext:   The raw key from the Authorization header
        stored_hash: The bcrypt hash retrieved from the database

    Returns:
        True if the key matches, False otherwise
    """
    try:
        return bcrypt.checkpw(plaintext.encode(), stored_hash.encode())
    except Exception:
        # Malformed hash in DB or encoding error — treat as invalid
        return False


def make_cache_key(plaintext: str) -> str:
    """
    Derive a safe in-memory cache lookup key from a plaintext API key.

    We SHA-256 the plaintext so the cache dict never holds the raw secret.
    SHA-256 is fast (not a concern here — we only call this to look up the
    cache, not for security hardening) and collision-resistant enough that
    two different keys will not share a cache entry.

    Args:
        plaintext: The raw dsrs- prefixed key

    Returns:
        64-char hex digest
    """
    return hashlib.sha256(plaintext.encode()).hexdigest()


def is_valid_api_key_format(api_key: str) -> bool:
    """
    Validate API key format: dsrs- prefix + exactly 32 base62 characters.

    Used as a fast pre-check before hitting the database or bcrypt.
    """
    if not api_key or not isinstance(api_key, str):
        return False
    return bool(re.fullmatch(r"dsrs-[A-Za-z0-9]{32}", api_key))


def mask_api_key(api_key: str) -> str:
    """
    Mask an API key for safe display in logs.

    Format: dsrs-aB3xK9m...0gH  (prefix + first 7 random chars + last 3)
    """
    if not api_key or len(api_key) < len(_KEY_PREFIX) + 4:
        return "****"
    visible_start = api_key[: len(_KEY_PREFIX) + 7]
    visible_end = api_key[-3:]
    return f"{visible_start}...{visible_end}"

"""
utils/auth_cache.py
In-memory bcrypt verification cache

bcrypt.verify() is intentionally slow (~100-200ms at work factor 12).
Calling it on every LLM request would add unacceptable latency.

This cache stores recently verified keys with a TTL so bcrypt is only
called once per key per TTL window rather than on every request.

Security properties:
  - Cache keys are SHA-256 digests of the plaintext API key —
    the raw secret never sits in memory as a dict key
  - Cache values hold only the entity auth dict, not the plaintext key
  - TTL is 5 minutes — a nulled key will stop working within 5 minutes
    unless explicitly evicted (null operations call evict() immediately)
  - The cache is process-local and never persisted or shared across pods
"""

import time
from typing import Any, Dict, Optional

from utils.key_generator import make_cache_key

# 5-minute TTL — short enough to limit blast radius of a compromised key,
# long enough to absorb the bcrypt cost on sustained traffic
_CACHE_TTL_SECONDS = 300


class AuthCache:
    def __init__(self):
        # { sha256(plaintext_key): {"entity": dict, "expires_at": float} }
        self._store: Dict[str, Dict] = {}

    def get(self, plaintext: str) -> Optional[Dict[str, Any]]:
        """
        Return the cached auth entity for this key if present and unexpired.

        Args:
            plaintext: Raw dsrs- prefixed API key from the request header

        Returns:
            Entity auth dict if cache hit and TTL valid, None otherwise
        """
        digest = make_cache_key(plaintext)
        entry = self._store.get(digest)

        if not entry:
            return None

        if time.time() > entry["expires_at"]:
            # Expired — evict lazily
            del self._store[digest]
            return None

        return entry["entity"]

    def set(self, plaintext: str, entity: Dict[str, Any]):
        """
        Cache a verified auth entity for this key.

        Args:
            plaintext: Raw dsrs- prefixed API key
            entity:    Auth dict returned by postgres_db.validate_api_key()
        """
        digest = make_cache_key(plaintext)
        self._store[digest] = {
            "entity": entity,
            "expires_at": time.time() + _CACHE_TTL_SECONDS,
        }

    def evict(self, plaintext: str):
        """
        Immediately remove a key from the cache.

        Called when an admin nulls a key so it stops working at once
        rather than lingering until TTL expiry.

        Args:
            plaintext: Raw dsrs- prefixed API key to evict
        """
        digest = make_cache_key(plaintext)
        self._store.pop(digest, None)

    def evict_by_identity(self, entity_type: str, identifier: str):
        """
        Evict all cache entries for a given entity identity.

        Used when we null a key but don't have the plaintext handy
        (e.g. admin nulling another user's key — we never saw their key).
        Scans the full cache, which is acceptable since the cache is small.

        Args:
            entity_type: 'user' or 'app'
            identifier:  net_id for users, app_name for apps
        """
        field = "user_net_id" if entity_type == "user" else "app_name"
        to_evict = [
            digest
            for digest, entry in self._store.items()
            if entry["entity"].get(field) == identifier
        ]
        for digest in to_evict:
            del self._store[digest]


# Global singleton — shared across all request handlers in the process
auth_cache = AuthCache()

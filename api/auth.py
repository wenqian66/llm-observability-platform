"""
api/auth.py
Authentication utilities

Three authentication paths:
  1. verify_api_key_header  — standard dsrs- key via Authorization: Bearer
     Hits the in-memory bcrypt cache first; falls back to DB verify on miss.

  2. verify_bootstrap_key   — bootstrap secret from .env
     Used only by POST /admin/keys/self-bootstrap.
     No cache — this is a static secret, not a bcrypt-hashed key.

  3. require_admin           — depends on verify_api_key_header + is_admin check
"""

from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException

from config import settings
from database.postgres import postgres_db
from utils.auth_cache import auth_cache
from utils.key_generator import is_valid_api_key_format
from utils.logger import logger


async def verify_api_key_header(
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    Verify a dsrs- prefixed API key from the Authorization header.

    Cache hit  → entity dict returned immediately (~1ms)
    Cache miss → bcrypt scan of DB (~100-200ms), result cached for 5 min

    Returns entity dict:
      entity_type            : 'user' or 'app'
      user_net_id            : net_id if user, else None
      app_name               : app_name if app, else None
      is_admin               : bool
      semantic_cache_enabled : bool
      display_name           : human-readable label for logging
    """
    if not settings.enable_auth:
        return {
            "api_key": "mock-test-api-key",
            "entity_type": "app",
            "user_net_id": None,
            "app_name": "test_app",
            "is_admin": True,
            "semantic_cache_enabled": False,
            "display_name": "Test App",
        }

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Use: Bearer <api_key>",
        )

    api_key = parts[1]

    # Fast format check before touching cache or DB
    if not is_valid_api_key_format(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    # Cache hit — skip bcrypt entirely
    cached = auth_cache.get(api_key)
    if cached:
        return cached

    # Cache miss — full bcrypt verification against DB
    entity = await postgres_db.validate_api_key(api_key)
    if not entity:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    # Warm the cache so subsequent requests in the TTL window are fast
    auth_cache.set(api_key, entity)
    return entity


def verify_bootstrap_key(authorization: Optional[str]) -> None:
    """
    Verify the bootstrap secret from the Authorization header.

    Raises HTTPException on any failure. Returns None on success —
    callers only need to know it passed.

    The bootstrap key is a static .env secret, not a bcrypt hash,
    so verification is a simple constant-time string comparison.
    """
    if not settings.bootstrap_api_key:
        raise HTTPException(
            status_code=403,
            detail="Bootstrap is disabled — BOOTSTRAP_API_KEY is not set",
        )

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Use: Bearer <bootstrap_api_key>",
        )

    # Use compare_digest to prevent timing attacks on the secret comparison
    import hmac

    if not hmac.compare_digest(parts[1], settings.bootstrap_api_key):
        logger.warning("⚠️  [BOOTSTRAP] Invalid bootstrap key attempted")
        raise HTTPException(status_code=401, detail="Invalid bootstrap key")


async def require_admin(
    auth: Dict[str, Any] = Depends(verify_api_key_header),
) -> Dict[str, Any]:
    """
    Require admin privileges.

    Only users with is_admin=True qualify — apps can never be admins.
    """
    if not settings.enable_auth:
        return auth

    if auth.get("entity_type") != "user":
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required. Apps cannot access admin endpoints.",
        )

    if not auth.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    return auth

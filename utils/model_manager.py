"""
utils/model_manager.py
Manages model auto-detection and caching from upstream LLMs
"""

import asyncio
import time
from typing import Dict, Optional

import httpx

from utils.logger import logger

# 60-minute TTL — models change rarely (deploys/swaps), so a long cache is safe.
# Use POST /admin/models/refresh to force an immediate refresh if a model swap happens.
_MODEL_CACHE_TTL = 3600


class ModelManager:
    """
    Automatically detects and caches available models from upstream LLMs.

    Keeps a per-LLM-type cache to avoid repeated upstream calls.
    Cache is in-memory — resets on pod restart, which triggers a fresh
    fetch on the first request. Falls back to a configurable default
    if detection fails.
    """

    def __init__(self):
        self.cache: Dict[str, Dict] = {}
        # Format: {"general": {"model": "gpt-oss-120b", "updated_at": 1234567890}, ...}
        self.cache_ttl = _MODEL_CACHE_TTL
        self.lock = asyncio.Lock()

    async def get_default_model(
        self,
        llm_type: str,
        llm_url: str,
        llm_api_key: str,
        fallback: str = "gpt-oss-120b",
    ) -> str:
        """
        Get the default model for this LLM type.

        Returns the cached model if still fresh (within 60 minutes),
        otherwise fetches from upstream and updates the cache.
        Falls back to the provided fallback string if detection fails.

        Args:
            llm_type: "general" or "coding"
            llm_url: Base URL of upstream LLM
            llm_api_key: API key for upstream LLM
            fallback: Model name to use if upstream is unreachable

        Returns:
            Model ID string (e.g. "gpt-oss-120b", "minimax-2.5")
        """
        async with self.lock:
            # Serve from cache if within TTL
            if llm_type in self.cache:
                cached = self.cache[llm_type]
                age = time.time() - cached["updated_at"]
                if age < self.cache_ttl:
                    logger.info(
                        f"📦 [MODEL CACHE HIT] {llm_type}: {cached['model']} "
                        f"(age: {int(age)}s / ttl: {self.cache_ttl}s)"
                    )
                    return cached["model"]

            # Cache miss or expired — fetch from upstream
            logger.info(f"🔍 [MODEL FETCH] Detecting model for {llm_type}...")

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{llm_url}/models",
                        headers={"Authorization": f"Bearer {llm_api_key}"},
                    )
                    response.raise_for_status()
                    data = response.json()

                    if "data" in data and len(data["data"]) > 0:
                        model = data["data"][0]["id"]

                        self.cache[llm_type] = {
                            "model": model,
                            "updated_at": time.time(),
                        }

                        logger.info(f"✅ [MODEL DETECTED] {llm_type}: {model}")
                        return model
                    else:
                        logger.warning(
                            f"⚠️  No models found in upstream response for {llm_type}"
                        )

            except Exception as e:
                logger.error(f"❌ [MODEL FETCH FAILED] {llm_type}: {e}")

            # Fallback — upstream unreachable or returned no models
            logger.warning(f"⚠️  [MODEL FALLBACK] {llm_type}: using {fallback}")
            return fallback

    def invalidate_cache(self, llm_type: Optional[str] = None):
        """
        Invalidate the model cache to force a fresh fetch on next request.
        Called by POST /admin/models/refresh — use this after a model swap.

        Args:
            llm_type: Invalidate only this type, or None to invalidate all
        """
        if llm_type:
            self.cache.pop(llm_type, None)
            logger.info(f"🗑️  Invalidated model cache for {llm_type}")
        else:
            self.cache.clear()
            logger.info("🗑️  Invalidated all model caches")


# Global instance
model_manager = ModelManager()

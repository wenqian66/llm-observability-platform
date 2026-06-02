"""
task_queue/redis_client.py
Redis client for queue management, active-slot tracking, and response delivery.

Active slot tracking uses Redis SADD/TTL instead of in-memory counters so that
slot state is shared across multiple FastAPI worker processes and survives restarts.

Queue model per account (entity_type + identifier):
  - Active set:  active:{entity_key} — SADD request_id, 120s TTL per member
  - Queued list: queued:{entity_key} — LPUSH/RPOP FIFO
  - Limits:      user accounts: 1 active + 2 queued
                 app accounts:  3 active + 6 queued

Global processing queue (per LLM type):
  - llm_queue:{llm_type} — RPUSH/LPOP FIFO, consumed by the processor
"""

import json
from typing import Any, Dict, Optional, Tuple

import redis.asyncio as aioredis

from utils.logger import logger

_RESPONSE_TTL = 360
_ACTIVE_SLOT_TTL = 120  # 2-minute timeout for active requests


RATE_LIMITS = {
    "user": {"active": 1, "queued": 2},
    "app": {"active": 3, "queued": 6},
}


class RedisClient:
    def __init__(self):
        self.client: aioredis.Redis = None

    async def connect(self, host: str, port: int, db: int = 0, password: str = ""):
        redis_url = f"redis://{host}:{port}/{db}"
        self.client = await aioredis.from_url(
            redis_url,
            password=password if password else None,
            decode_responses=True,
        )
        logger.info(f"Redis connected: {host}:{port}/{db}")

    async def close(self):
        if self.client:
            await self.client.close()
        logger.info("Redis connection closed")

    # ------------------------------------------------------------------
    # Entity key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entity_key(entity_type: str, identifier: str) -> str:
        return f"{entity_type}:{identifier}"

    def _active_set_key(self, entity_key: str) -> str:
        return f"active:{entity_key}"

    def _queued_list_key(self, entity_key: str) -> str:
        return f"queued:{entity_key}"

    def _active_member_key(self, request_id: str) -> str:
        return f"active_member:{request_id}"

    # ------------------------------------------------------------------
    # Rate-limit check + enqueue
    # ------------------------------------------------------------------

    async def check_and_enqueue(
        self,
        llm_type: str,
        request_data: Dict[str, Any],
        entity_type: str,
        identifier: str,
    ) -> Tuple[bool, str]:
        """
        Check per-account rate limits and enqueue if allowed.

        Returns (accepted, status) where status is 'active', 'queued', or 'rejected'.
        """
        entity_key = self._entity_key(entity_type, identifier)
        active_key = self._active_set_key(entity_key)
        queued_key = self._queued_list_key(entity_key)
        request_id = request_data["request_id"]
        limits = RATE_LIMITS.get(entity_type, RATE_LIMITS["user"])

        active_count = await self.client.scard(active_key)
        queued_count = await self.client.llen(queued_key)

        if active_count < limits["active"]:
            # Slot available — activate immediately
            await self.client.sadd(active_key, request_id)
            # Per-member TTL tracked via a separate key (sets don't have per-member TTL)
            await self.client.setex(self._active_member_key(request_id), _ACTIVE_SLOT_TTL, entity_key)
            await self._enqueue_to_processor(llm_type, request_data)
            logger.info(f"[ACTIVE] {request_id[:8]} | {entity_key} ({active_count + 1}/{limits['active']})")
            return True, "active"

        if queued_count < limits["queued"]:
            # Queue the request for later promotion
            await self.client.rpush(queued_key, json.dumps(request_data))
            # Also store the llm_type so promotion knows where to route
            await self.client.hset(f"queued_meta:{request_id}", mapping={
                "llm_type": llm_type,
                "entity_key": entity_key,
            })
            logger.info(f"[QUEUED] {request_id[:8]} | {entity_key} ({queued_count + 1}/{limits['queued']})")
            return True, "queued"

        logger.warning(f"[REJECTED] {request_id[:8]} | {entity_key} — at capacity")
        return False, "rejected"

    async def _enqueue_to_processor(self, llm_type: str, request_data: Dict[str, Any]):
        queue_key = f"llm_queue:{llm_type}"
        await self.client.rpush(queue_key, json.dumps(request_data))

    # ------------------------------------------------------------------
    # Active slot release + promotion
    # ------------------------------------------------------------------

    async def release_active_slot(self, request_id: str):
        """Release an active slot and promote the next queued request."""
        member_key = self._active_member_key(request_id)
        entity_key = await self.client.getdel(member_key)
        if not entity_key:
            return

        active_key = self._active_set_key(entity_key)
        await self.client.srem(active_key, request_id)
        await self._promote_next(entity_key)

    async def _promote_next(self, entity_key: str):
        """RPOP the oldest queued request and activate it."""
        queued_key = self._queued_list_key(entity_key)
        data = await self.client.lpop(queued_key)
        if not data:
            return

        request_data = json.loads(data)
        request_id = request_data["request_id"]

        meta = await self.client.hgetall(f"queued_meta:{request_id}")
        await self.client.delete(f"queued_meta:{request_id}")
        llm_type = meta.get("llm_type", "general")

        active_key = self._active_set_key(entity_key)
        await self.client.sadd(active_key, request_id)
        await self.client.setex(self._active_member_key(request_id), _ACTIVE_SLOT_TTL, entity_key)
        await self._enqueue_to_processor(llm_type, request_data)
        logger.info(f"[PROMOTED] {request_id[:8]} | {entity_key}")

    # ------------------------------------------------------------------
    # Expired slot cleanup (called by background task)
    # ------------------------------------------------------------------

    async def cleanup_expired_slots(self):
        """
        Scan active sets for members whose TTL key has expired.
        Remove them from the set and promote the next queued request.
        """
        cursor = "0"
        while True:
            cursor, keys = await self.client.scan(cursor=cursor, match="active:*", count=100)
            for active_key in keys:
                members = await self.client.smembers(active_key)
                for request_id in members:
                    exists = await self.client.exists(self._active_member_key(request_id))
                    if not exists:
                        # TTL expired — slot timed out after 2 minutes
                        await self.client.srem(active_key, request_id)
                        entity_key = active_key.removeprefix("active:")
                        logger.info(f"[EXPIRED] {request_id[:8]} | {entity_key}")
                        await self._promote_next(entity_key)
            if cursor == "0" or cursor == 0:
                break

    # ------------------------------------------------------------------
    # Global queue operations (used by processor)
    # ------------------------------------------------------------------

    async def dequeue(self, llm_type: str) -> Optional[Dict[str, Any]]:
        queue_key = f"llm_queue:{llm_type}"
        data = await self.client.lpop(queue_key)
        if data:
            return json.loads(data)
        return None

    async def get_queue_length(self, llm_type: str) -> int:
        queue_key = f"llm_queue:{llm_type}"
        return await self.client.llen(queue_key)

    # ------------------------------------------------------------------
    # Response delivery bus
    # ------------------------------------------------------------------

    async def store_response(self, request_id: str, response_data: Dict[str, Any]):
        key = f"response:{request_id}"
        await self.client.setex(key, _RESPONSE_TTL, json.dumps(response_data))

    async def get_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        key = f"response:{request_id}"
        data = await self.client.getdel(key)
        if data:
            return json.loads(data)
        return None

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def set_cancel_flag(self, request_id: str):
        await self.client.setex(f"cancel:{request_id}", 60, "1")

    async def is_cancelled(self, request_id: str) -> bool:
        return await self.client.exists(f"cancel:{request_id}") > 0


redis_client = RedisClient()

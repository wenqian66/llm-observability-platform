"""
database/timescale.py
TimescaleDB operations for request logging and metrics

Schema must be initialized manually via database_init/timescale_init.sql before first run.

Connection pool resilience:
  max_inactive_connection_lifetime=300 — asyncpg proactively recycles idle
    connections every 5 minutes, before the server closes them server-side.
    Prevents CancelledError / TimeoutError on SSL handshake after long idle
    periods (observed after ~hours of low traffic).
  command_timeout=60 — any query hanging beyond 60s raises a clean exception
    instead of blocking indefinitely.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import asyncpg

from utils.logger import logger


class TimescaleDB:
    def __init__(self):
        self.pool: asyncpg.Pool = None

    async def connect(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        min_size: int = 5,
        max_size: int = 20,
    ):
        """
        Initialize connection pool.

        NOTE: TimescaleDB schema is NOT auto-initialized on connect.
        Run database_init/timescale_init.sql manually before first deploy:
            psql -h <host> -U <user> -d <database> -f timescale_init.sql
        """
        if self.pool is not None:
            return
        try:
            self.pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                min_size=min_size,
                max_size=max_size,
                max_inactive_connection_lifetime=300,
            )
            logger.info(f"✅ TimescaleDB connected: {host}:{port}/{database}")
        except Exception as e:
            logger.error(f"❌ TimescaleDB connection failed: {e}")
            raise

    async def close(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("✅ TimescaleDB connection closed")

    async def log_request(
        self,
        request_id: str,
        entity_type: str,  # 'user' or 'app'
        user_net_id: Optional[str],  # set if entity_type='user', else None
        app_name: Optional[str],  # set if entity_type='app', else None
        api_key: str,
        llm_type: str,
        requested_model: Optional[
            str
        ],  # what the client passed (may be 'auto' or None)
        messages: List[Dict],
        temperature: float,
        max_tokens: int,
        semantic_cache_enabled: bool = True,
    ):
        """
        Insert a new pending request log.

        resolved_model is intentionally absent here — it isn't known until
        the processor fires. It is set later via update_request_success().
        """
        try:
            current_time = datetime.now(timezone.utc)

            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO llm_request_logs (
                        timestamp, request_id,
                        entity_type, user_net_id, app_name,
                        llm_type, requested_model,
                        input_messages, temperature, max_tokens,
                        semantic_cache_enabled, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    """,
                    current_time,
                    uuid.UUID(request_id),
                    entity_type,
                    user_net_id,
                    app_name,
                    llm_type,
                    requested_model,
                    json.dumps(messages),
                    temperature,
                    max_tokens,
                    semantic_cache_enabled,
                    "pending",
                )

            entity_label = user_net_id if entity_type == "user" else app_name
            logger.info(
                f"📝 [TIMESCALE] Request logged: {request_id[:8]}... | "
                f"{entity_type}:{entity_label} | Cache: {semantic_cache_enabled}"
            )
            return True

        except Exception as e:
            logger.error(
                f"❌ [TIMESCALE] Failed to log request {request_id[:8]}...: {e}"
            )
            return False

    async def batch_insert(self, records: List[Dict]):
        """
        Batch-insert pending request logs. Called by the Celery worker
        when the buffer hits 100 records or every 5 seconds.
        """
        if not records:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO llm_request_logs (
                        timestamp, request_id, entity_type, user_net_id, app_name,
                        llm_type, requested_model, input_messages, temperature, max_tokens,
                        semantic_cache_enabled, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    """,
                    [
                        (
                            r.get("timestamp", datetime.now(timezone.utc)),
                            uuid.UUID(r["request_id"]),
                            r["entity_type"],
                            r.get("user_net_id"),
                            r.get("app_name"),
                            r["llm_type"],
                            r.get("requested_model"),
                            json.dumps(r["messages"]),
                            r.get("temperature", 0.7),
                            r.get("max_tokens", 4096),
                            r.get("semantic_cache_enabled", False),
                            "pending",
                        )
                        for r in records
                    ],
                )
            logger.info(f"[TIMESCALE] Batch inserted {len(records)} records")
        except Exception as e:
            logger.error(f"[TIMESCALE] Batch insert failed: {e}")
            raise

    async def update_request_success(
        self,
        request_id: str,
        response_content: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        llm_latency_ms: int,
        queue_time_ms: int,
        resolved_model: str,  # actual model used by the processor
        cache_hit: bool = False,
        reasoning_content: Optional[str] = None,
        upstream_url: Optional[str] = None,
    ):
        """
        Update a pending request log with success outcome.

        resolved_model is set here rather than at log_request() time because
        auto-detection happens inside the processor, after the row is inserted.
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE llm_request_logs SET
                        status            = 'success',
                        resolved_model    = $2,
                        cache_hit         = $3,
                        reasoning_content = $4,
                        response_content  = $5,
                        input_tokens      = $6,
                        output_tokens     = $7,
                        total_tokens      = $8,
                        latency_ms        = $9,
                        llm_latency_ms    = $10,
                        queue_time_ms     = $11,
                        upstream_url      = $12
                    WHERE request_id = $1
                    """,
                    uuid.UUID(request_id),
                    resolved_model,
                    cache_hit,
                    reasoning_content,
                    response_content,
                    input_tokens,
                    output_tokens,
                    input_tokens + output_tokens,
                    latency_ms,
                    llm_latency_ms,
                    queue_time_ms,
                    upstream_url,
                )

            logger.info(
                f"✅ [TIMESCALE] Updated {request_id[:8]}... | "
                f"Model: {resolved_model} | Cache: {cache_hit}"
            )
            return True

        except Exception as e:
            logger.error(
                f"❌ [TIMESCALE] Failed to update request {request_id[:8]}...: {e}",
                exc_info=True,
            )
            return False

    async def update_request_error(
        self,
        request_id: str,
        error_message: str,
        status: str = "error",  # 'error', 'timeout', 'connection_failed'
        queue_time_ms: Optional[int] = None,
    ):
        """Update a pending request log with error outcome."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE llm_request_logs SET
                        status        = $2,
                        error_message = $3,
                        queue_time_ms = $4
                    WHERE request_id = $1
                    """,
                    uuid.UUID(request_id),
                    status,
                    error_message,
                    queue_time_ms,
                )

            logger.error(
                f"❌ [TIMESCALE] Error logged: {request_id[:8]}... | "
                f"Status: {status} | Error: {error_message[:100]}"
            )
            return True

        except Exception as e:
            logger.error(
                f"❌ [TIMESCALE] Failed to log error for {request_id[:8]}...: {e}",
                exc_info=True,
            )
            return False

    async def get_recent_logs(self, limit: int = 10):
        """Get recent request logs for debugging."""
        try:
            async with self.pool.acquire() as conn:
                logs = await conn.fetch(
                    """
                    SELECT
                        request_id,
                        entity_type,
                        user_net_id,
                        app_name,
                        requested_model,
                        resolved_model,
                        status,
                        cache_hit,
                        semantic_cache_enabled,
                        input_tokens,
                        output_tokens,
                        total_tokens,
                        LENGTH(reasoning_content)  AS reasoning_length,
                        LENGTH(response_content)   AS response_length,
                        SUBSTRING(reasoning_content, 1, 100) AS reasoning_preview,
                        SUBSTRING(response_content,  1, 100) AS response_preview,
                        latency_ms,
                        llm_latency_ms,
                        queue_time_ms,
                        timestamp
                    FROM llm_request_logs
                    ORDER BY timestamp DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(log) for log in logs]
        except Exception as e:
            logger.error(f"❌ [TIMESCALE] Failed to fetch recent logs: {e}")
            return []


# Global instance
timescale_db = TimescaleDB()

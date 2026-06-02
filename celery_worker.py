"""
celery_worker.py
Celery worker with batch-insert log buffer.

Flushes to TimescaleDB every 5 seconds OR when buffer hits 100 records (whichever first).
Uses Celery Beat to schedule the periodic flush.
"""

import asyncio
import os
from typing import Dict, List

from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("echolog", broker=REDIS_URL, backend=REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "flush-log-buffer": {
            "task": "celery_worker.flush_log_buffer",
            "schedule": 5.0,
        },
    },
)

_buffer: List[Dict] = []
BUFFER_MAX = 100


async def _flush_async(records: List[Dict]):
    from database.timescale import timescale_db
    # connect() is idempotent — skips if pool already exists
    await timescale_db.connect(
        host=os.getenv("TIMESCALE_HOST", "localhost"),
        port=int(os.getenv("TIMESCALE_PORT", "5432")),
        database=os.getenv("TIMESCALE_DB", "llm_metrics"),
        user=os.getenv("TIMESCALE_USER", "admin"),
        password=os.getenv("TIMESCALE_PASSWORD", ""),
    )
    await timescale_db.batch_insert(records)


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@celery_app.task(name="celery_worker.buffer_log")
def buffer_log(record: Dict):
    _buffer.append(record)
    if len(_buffer) >= BUFFER_MAX:
        _do_flush()


@celery_app.task(name="celery_worker.flush_log_buffer")
def flush_log_buffer():
    _do_flush()


def _do_flush():
    if not _buffer:
        return
    batch = _buffer.copy()
    _buffer.clear()
    _run_async(_flush_async(batch))

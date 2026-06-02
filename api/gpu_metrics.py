"""
api/gpu_metrics.py
GPU / vLLM health metrics — KV cache usage and active request count

Architecture:
  - Background scraper (started in gateway_server.py lifespan) polls each
    vLLM /metrics endpoint every 1 second and writes into LLMGpuStats objects.
  - GET /gpu-metrics reads those objects synchronously — zero network calls
    on the request path, so the frontend always gets an instant response.
  - Staleness guard: if last_updated is >5s old (hung scraper), status is
    forced to "stale" so the UI never renders a frozen value as live data.

Scrape URLs come directly from settings:
  GENERAL_LLM_METRICS_URL = http://llm.ai-models.svc.cluster.local:8000/metrics
  CODING_LLM_METRICS_URL  = http://llm-code.ai-models.svc.cluster.local:8000/metrics
  These are internal ClusterIP addresses — not routed through Traefik.

Metrics extracted from Prometheus text format:
  vllm:kv_cache_usage_perc  — float 0.0–1.0, multiplied by 100 for display
  vllm:num_requests_running — int, active inference slots in use

Regex note:
  Both patterns use re.MULTILINE with ^ to anchor to line start.
  This prevents false matches against vllm:cache_config_info, which contains
  the string "kv_cache" inside its label set and would otherwise match the
  broader pattern — incorrectly reporting 100% cache usage.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from utils.logger import logger

router = APIRouter(tags=["gpu-metrics"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How often the background scraper hits each vLLM /metrics endpoint.
_SCRAPE_INTERVAL_S = 1.0

# If last_updated is older than this, the endpoint surfaces "stale" status
# even if the scraper object still holds its last known values.
_STALENESS_THRESHOLD_S = 5.0

# Prometheus metric name patterns.
# Anchored to line start (re.MULTILINE) so vllm:cache_config_info lines —
# which contain "kv_cache" inside label keys — cannot produce false matches.
_RE_KV_CACHE = re.compile(
    r"^vllm:kv_cache_usage_perc\{[^}]*\}\s+([\d.]+)",
    re.MULTILINE,
)
_RE_REQUESTS_RUNNING = re.compile(
    r"^vllm:num_requests_running\{[^}]*\}\s+([\d.]+)",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class LLMGpuStats:
    """
    Mutable state object written by the scraper and read by the endpoint.

    One instance per LLM type (general, coding). Mutated in-place by the
    scraper loop — no locks needed because Python's GIL makes individual
    attribute assignments atomic for simple types.
    """

    llm_type: str
    status: str = "initializing"  # "initializing" | "active" | "offline" | "stale"
    kv_cache_usage_perc: Optional[float] = None  # 0.0 – 100.0
    num_requests_running: Optional[int] = None
    last_updated: Optional[datetime] = None

    def set_active(self, kv: float, running: int):
        self.status = "active"
        self.kv_cache_usage_perc = round(kv * 100, 1)  # raw vLLM value is 0–1
        self.num_requests_running = running
        self.last_updated = datetime.now(timezone.utc)

    def set_offline(self):
        self.status = "offline"
        self.kv_cache_usage_perc = None
        self.num_requests_running = None
        self.last_updated = datetime.now(timezone.utc)

    def is_stale(self) -> bool:
        if self.last_updated is None:
            return False  # still "initializing" — not stale, just not ready
        age = (datetime.now(timezone.utc) - self.last_updated).total_seconds()
        return age > _STALENESS_THRESHOLD_S


# Module-level singletons — created once, shared between scraper and router.
gpu_stats: dict[str, LLMGpuStats] = {
    "general": LLMGpuStats(llm_type="general"),
    "coding": LLMGpuStats(llm_type="coding"),
}


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


async def scrape_loop(llm_type: str, scrape_url: str):
    """
    Persistent background task that polls one vLLM /metrics endpoint.

    Runs for the lifetime of the process. A fresh httpx.AsyncClient is
    reused across iterations to benefit from connection keep-alive.
    On any error (timeout, connection refused, parse failure) the stats
    object is marked offline and the loop continues after the interval.
    """
    logger.info(f"📡 [GPU MONITOR] Starting scraper for {llm_type}: {scrape_url}")
    stats = gpu_stats[llm_type]

    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            try:
                response = await client.get(scrape_url)
                response.raise_for_status()
                text = response.text

                kv_match = _RE_KV_CACHE.search(text)
                run_match = _RE_REQUESTS_RUNNING.search(text)

                if kv_match and run_match:
                    stats.set_active(
                        kv=float(kv_match.group(1)),
                        running=int(float(run_match.group(1))),
                    )
                else:
                    # Endpoint responded but expected metrics absent —
                    # vLLM may not have loaded a model yet.
                    logger.warning(
                        f"⚠️  [GPU MONITOR] {llm_type}: metrics not found in response"
                    )
                    stats.set_offline()

            except httpx.TimeoutException:
                logger.warning(f"⚠️  [GPU MONITOR] {llm_type}: scrape timeout")
                stats.set_offline()

            except (httpx.ConnectError, httpx.NetworkError) as e:
                logger.warning(f"⚠️  [GPU MONITOR] {llm_type}: connection error — {e}")
                stats.set_offline()

            except Exception as e:
                logger.error(f"❌ [GPU MONITOR] {llm_type}: unexpected error — {e}")
                stats.set_offline()

            await asyncio.sleep(_SCRAPE_INTERVAL_S)


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class LLMGpuStatsResponse(BaseModel):
    status: str
    kv_cache_usage_perc: Optional[float]
    num_requests_running: Optional[int]
    last_updated: Optional[datetime]


class GpuMetricsResponse(BaseModel):
    general: LLMGpuStatsResponse
    coding: LLMGpuStatsResponse


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/gpu-metrics", response_model=GpuMetricsResponse)
async def get_gpu_metrics():
    """
    Live KV cache usage and active request count for both vLLM backends.

    No auth required — same policy as /health and /metrics/*.
    Reads from in-memory scraper state; never calls vLLM directly.

    Status values:
      initializing — scraper has not yet completed its first successful poll
      active       — fresh reading within the last 5 seconds
      stale        — scraper loop appears hung (last_updated >5s ago)
      offline      — vLLM endpoint unreachable or returning errors
    """

    def _to_response(stats: LLMGpuStats) -> LLMGpuStatsResponse:
        # Apply staleness check — overrides whatever status the scraper set
        # if the last successful write is too old.
        effective_status = "stale" if stats.is_stale() else stats.status
        return LLMGpuStatsResponse(
            status=effective_status,
            kv_cache_usage_perc=stats.kv_cache_usage_perc,
            num_requests_running=stats.num_requests_running,
            last_updated=stats.last_updated,
        )

    return GpuMetricsResponse(
        general=_to_response(gpu_stats["general"]),
        coding=_to_response(gpu_stats["coding"]),
    )

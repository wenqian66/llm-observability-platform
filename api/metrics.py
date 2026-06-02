"""
api/metrics.py
Metrics API Router — Echo Log UI dashboard

Scorecard endpoints — filtered by calendar period in America/Chicago:
  GET /metrics/all?range=1d    → since midnight today CDT/CST
  GET /metrics/all?range=1w    → since Monday midnight CDT/CST
  GET /metrics/all?range=1m    → since 1st of month midnight CDT/CST
  GET /metrics/all?range=all   → all time
  (same pattern for /metrics/apps and /metrics/users)

Chart endpoints — always all-time, never filtered by range:
  GET /metrics/all/chart/daily-pattern
  GET /metrics/apps/chart/daily-pattern
  GET /metrics/users/chart/daily-pattern

All timestamps and time bucketing use America/Chicago (CDT/CST).
"1 Day"   = since midnight tonight         — not rolling 24h.
"1 Week"  = since last Monday midnight     — not rolling 7 days.
"1 Month" = since 1st of current month     — not rolling 30 days.

Security note: all queries use fully static SQL with $N parameters.
No user input is ever interpolated into query strings.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database.timescale import timescale_db
from utils.logger import logger

router = APIRouter(prefix="/metrics", tags=["metrics"])

CHICAGO_TZ = "America/Chicago"

# ---------------------------------------------------------------------------
# Static query map — one fully pre-written query per (endpoint, range).
#
# Why static strings instead of f-string interpolation?
# The time filter is structural SQL (a WHERE clause fragment), not a value,
# so parameterised queries ($1/$2) can't carry it. The safe alternative is
# to pre-write every variant at module load time and select by key at
# request time. No user input ever touches a query string.
# ---------------------------------------------------------------------------

_RANGE_FILTERS = {
    "1d": f"(timestamp AT TIME ZONE '{CHICAGO_TZ}') >= date_trunc('day',   NOW() AT TIME ZONE '{CHICAGO_TZ}')",
    "1w": f"(timestamp AT TIME ZONE '{CHICAGO_TZ}') >= date_trunc('week',  NOW() AT TIME ZONE '{CHICAGO_TZ}')",
    "1m": f"(timestamp AT TIME ZONE '{CHICAGO_TZ}') >= date_trunc('month', NOW() AT TIME ZONE '{CHICAGO_TZ}')",
    "all": "TRUE",
}


COST_PER_1K_TOKENS = {
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
}
DEFAULT_COST = {"input": 0.002, "output": 0.006}


def _build_all_metrics_query(range_key: str) -> str:
    return f"""
    SELECT
        COUNT(*) FILTER (WHERE status = 'success')  AS requests_served,
        COUNT(*) FILTER (WHERE status = 'error')    AS requests_failed,
        COUNT(*) FILTER (WHERE status = 'timeout')  AS requests_timeout,
        COALESCE(ROUND(AVG(input_tokens)),  0)       AS avg_input_tokens,
        COALESCE(ROUND(AVG(output_tokens)), 0)       AS avg_output_tokens,
        COALESCE(SUM(input_tokens), 0)               AS total_input_tokens,
        COALESCE(SUM(output_tokens), 0)              AS total_output_tokens,
        COALESCE(ROUND(AVG(latency_ms) FILTER (WHERE status = 'success')), 0) AS avg_latency
    FROM llm_request_logs
    WHERE {_RANGE_FILTERS[range_key]};
    """


def _build_apps_metrics_query(range_key: str) -> str:
    f = _RANGE_FILTERS[range_key]
    return f"""
    WITH time_filtered AS (
        SELECT *
        FROM llm_request_logs
        WHERE entity_type = 'app'
          AND app_name IS NOT NULL
          AND {f}
    ),
    app_counts AS (
        SELECT app_name, COUNT(*) AS request_count
        FROM time_filtered
        GROUP BY app_name
    )
    SELECT
        COUNT(DISTINCT app_name)                                                AS apps_connected,
        COUNT(DISTINCT app_name)                                                AS active_apps,

        (SELECT app_name     FROM app_counts ORDER BY request_count DESC LIMIT 1) AS max_app_name,
        (SELECT request_count FROM app_counts ORDER BY request_count DESC LIMIT 1) AS max_app_count,
        (SELECT app_name     FROM app_counts ORDER BY request_count ASC  LIMIT 1) AS min_app_name,
        (SELECT request_count FROM app_counts ORDER BY request_count ASC  LIMIT 1) AS min_app_count,

        COUNT(*) FILTER (WHERE status = 'success')  AS requests_served,
        COUNT(*) FILTER (WHERE status = 'error')    AS requests_failed,
        COUNT(*) FILTER (WHERE status = 'timeout')  AS requests_timeout,

        COALESCE(ROUND(AVG(input_tokens)),  0)       AS avg_input_tokens,
        COALESCE(ROUND(AVG(output_tokens)), 0)       AS avg_output_tokens,
        COALESCE(ROUND(AVG(latency_ms) FILTER (WHERE status = 'success')), 0) AS avg_latency
    FROM time_filtered;
    """


def _build_users_metrics_query(range_key: str) -> str:
    f = _RANGE_FILTERS[range_key]
    return f"""
    WITH time_filtered AS (
        SELECT *
        FROM llm_request_logs
        WHERE entity_type = 'user'
          AND user_net_id IS NOT NULL
          AND {f}
    ),
    user_counts AS (
        SELECT user_net_id, COUNT(*) AS request_count
        FROM time_filtered
        GROUP BY user_net_id
    )
    SELECT
        COUNT(DISTINCT user_net_id)                                                   AS users_connected,
        COUNT(DISTINCT user_net_id)                                                   AS active_users,

        (SELECT user_net_id   FROM user_counts ORDER BY request_count DESC LIMIT 1)  AS max_user_name,
        (SELECT request_count FROM user_counts ORDER BY request_count DESC LIMIT 1)  AS max_user_count,
        (SELECT user_net_id   FROM user_counts ORDER BY request_count ASC  LIMIT 1)  AS min_user_name,
        (SELECT request_count FROM user_counts ORDER BY request_count ASC  LIMIT 1)  AS min_user_count,

        COUNT(*) FILTER (WHERE status = 'success')  AS requests_served,
        COUNT(*) FILTER (WHERE status = 'error')    AS requests_failed,
        COUNT(*) FILTER (WHERE status = 'timeout')  AS requests_timeout,

        COALESCE(ROUND(AVG(input_tokens)),  0)       AS avg_input_tokens,
        COALESCE(ROUND(AVG(output_tokens)), 0)       AS avg_output_tokens,
        COALESCE(ROUND(AVG(latency_ms) FILTER (WHERE status = 'success')), 0) AS avg_latency
    FROM time_filtered;
    """


# Pre-build all query strings at module load — zero runtime string construction.
_ALL_METRICS_QUERIES = {r: _build_all_metrics_query(r) for r in _RANGE_FILTERS}
_APPS_METRICS_QUERIES = {r: _build_apps_metrics_query(r) for r in _RANGE_FILTERS}
_USERS_METRICS_QUERIES = {r: _build_users_metrics_query(r) for r in _RANGE_FILTERS}

# Chart queries — always all-time, no range parameter.
#
# DOW mapping note:
#   EXTRACT(DOW) returns 0=Sunday … 6=Saturday.
#   We map to Mon–Sun order in Python via _DOW_TO_DAY below,
#   so we never rely on TO_CHAR locale for day abbreviations.
_CHART_ALL_QUERY = f"""
SELECT
    EXTRACT(DOW FROM (timestamp AT TIME ZONE '{CHICAGO_TZ}')) AS day_num,
    COUNT(*) AS value
FROM llm_request_logs
GROUP BY day_num
ORDER BY day_num;
"""

_CHART_APPS_QUERY = f"""
SELECT
    EXTRACT(DOW FROM (timestamp AT TIME ZONE '{CHICAGO_TZ}')) AS day_num,
    COUNT(*) AS value
FROM llm_request_logs
WHERE entity_type = 'app' AND app_name IS NOT NULL
GROUP BY day_num
ORDER BY day_num;
"""

_CHART_USERS_QUERY = f"""
SELECT
    EXTRACT(DOW FROM (timestamp AT TIME ZONE '{CHICAGO_TZ}')) AS day_num,
    COUNT(*) AS value
FROM llm_request_logs
WHERE entity_type = 'user' AND user_net_id IS NOT NULL
GROUP BY day_num
ORDER BY day_num;
"""

# EXTRACT(DOW): 0=Sunday, 1=Monday … 6=Saturday → display Mon–Sun
_DOW_TO_DAY = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 0: "Sun"}
_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ============================================
# Response Models
# ============================================


class AllMetricsResponse(BaseModel):
    requests_served: int
    requests_failed: int
    requests_timeout: int
    avg_input_tokens: float
    avg_output_tokens: float
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float
    avg_latency: int
    timezone: str = CHICAGO_TZ


class AppsMetricsResponse(BaseModel):
    # Both counts are scoped to the selected time range
    apps_connected: int
    active_apps: int
    max_requests_app_name: Optional[str]  # full app name e.g. "atlas"
    max_requests_app_count: Optional[int]
    min_requests_app_name: Optional[str]
    min_requests_app_count: Optional[int]
    requests_served: int
    requests_failed: int
    requests_timeout: int
    avg_input_tokens: float
    avg_output_tokens: float
    avg_latency: int
    timezone: str = CHICAGO_TZ


class UsersMetricsResponse(BaseModel):
    # Both counts are scoped to the selected time range
    users_connected: int
    active_users: int
    max_requests_user_name: Optional[str]  # net_id e.g. "abhinay4"
    max_requests_user_count: Optional[int]
    min_requests_user_name: Optional[str]
    min_requests_user_count: Optional[int]
    requests_served: int
    requests_failed: int
    requests_timeout: int
    avg_input_tokens: float
    avg_output_tokens: float
    avg_latency: int
    timezone: str = CHICAGO_TZ


class DailyPatternPoint(BaseModel):
    day: str
    value: float


# ============================================
# Helper
# ============================================


def _format_daily_pattern(rows) -> List[DailyPatternPoint]:
    """
    Map EXTRACT(DOW) integer rows → Mon–Sun ordered DailyPatternPoints.

    Missing days (no data yet) are filled with 0.0.
    Day names come from the _DOW_TO_DAY constant — never from a DB locale.
    """
    day_values = {day: 0.0 for day in _DAY_ORDER}
    for row in rows:
        day_name = _DOW_TO_DAY.get(int(row["day_num"]))
        if day_name:
            day_values[day_name] = float(row["value"] or 0)
    return [DailyPatternPoint(day=day, value=day_values[day]) for day in _DAY_ORDER]


# ============================================
# Scorecard Endpoints
# ============================================


@router.get("/all", response_model=AllMetricsResponse)
async def get_all_metrics(range: str = Query("1d", pattern="^(1d|1w|1m|all)$")):
    """Aggregate metrics across all apps and users for the selected calendar period."""
    try:
        async with timescale_db.pool.acquire() as conn:
            row = await conn.fetchrow(_ALL_METRICS_QUERIES[range])

        total_in = int(row["total_input_tokens"] or 0)
        total_out = int(row["total_output_tokens"] or 0)
        cost = (total_in / 1000 * DEFAULT_COST["input"]) + (total_out / 1000 * DEFAULT_COST["output"])

        return AllMetricsResponse(
            requests_served=row["requests_served"] or 0,
            requests_failed=row["requests_failed"] or 0,
            requests_timeout=row["requests_timeout"] or 0,
            avg_input_tokens=float(row["avg_input_tokens"] or 0),
            avg_output_tokens=float(row["avg_output_tokens"] or 0),
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            estimated_cost_usd=round(cost, 4),
            avg_latency=int(row["avg_latency"] or 0),
        )
    except Exception as e:
        logger.error(f"Error fetching all metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/apps", response_model=AppsMetricsResponse)
async def get_apps_metrics(range: str = Query("1d", pattern="^(1d|1w|1m|all)$")):
    """
    App-specific metrics for the selected calendar period.

    apps_connected and active_apps are both scoped to the chosen time range —
    they count distinct apps that made at least one request in that period.
    The UI handles name truncation.
    """
    try:
        async with timescale_db.pool.acquire() as conn:
            row = await conn.fetchrow(_APPS_METRICS_QUERIES[range])

        return AppsMetricsResponse(
            apps_connected=row["apps_connected"] or 0,
            active_apps=row["active_apps"] or 0,
            max_requests_app_name=row["max_app_name"],
            max_requests_app_count=row["max_app_count"],
            min_requests_app_name=row["min_app_name"],
            min_requests_app_count=row["min_app_count"],
            requests_served=row["requests_served"] or 0,
            requests_failed=row["requests_failed"] or 0,
            requests_timeout=row["requests_timeout"] or 0,
            avg_input_tokens=float(row["avg_input_tokens"] or 0),
            avg_output_tokens=float(row["avg_output_tokens"] or 0),
            avg_latency=int(row["avg_latency"] or 0),
        )
    except Exception as e:
        logger.error(f"Error fetching apps metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users", response_model=UsersMetricsResponse)
async def get_users_metrics(range: str = Query("1d", pattern="^(1d|1w|1m|all)$")):
    """
    User-specific metrics for the selected calendar period.

    users_connected and active_users are both scoped to the chosen time range.
    The UI handles name truncation.
    """
    try:
        async with timescale_db.pool.acquire() as conn:
            row = await conn.fetchrow(_USERS_METRICS_QUERIES[range])

        return UsersMetricsResponse(
            users_connected=row["users_connected"] or 0,
            active_users=row["active_users"] or 0,
            max_requests_user_name=row["max_user_name"],
            max_requests_user_count=row["max_user_count"],
            min_requests_user_name=row["min_user_name"],
            min_requests_user_count=row["min_user_count"],
            requests_served=row["requests_served"] or 0,
            requests_failed=row["requests_failed"] or 0,
            requests_timeout=row["requests_timeout"] or 0,
            avg_input_tokens=float(row["avg_input_tokens"] or 0),
            avg_output_tokens=float(row["avg_output_tokens"] or 0),
            avg_latency=int(row["avg_latency"] or 0),
        )
    except Exception as e:
        logger.error(f"Error fetching users metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Chart Endpoints — always all-time
# ============================================


@router.get("/all/chart/daily-pattern", response_model=List[DailyPatternPoint])
async def get_all_daily_pattern():
    """Total requests by day of week — all time, all entities."""
    try:
        async with timescale_db.pool.acquire() as conn:
            rows = await conn.fetch(_CHART_ALL_QUERY)
        return _format_daily_pattern(rows)
    except Exception as e:
        logger.error(f"Error fetching all daily pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/apps/chart/daily-pattern", response_model=List[DailyPatternPoint])
async def get_apps_daily_pattern():
    """Total app requests by day of week — all time."""
    try:
        async with timescale_db.pool.acquire() as conn:
            rows = await conn.fetch(_CHART_APPS_QUERY)
        return _format_daily_pattern(rows)
    except Exception as e:
        logger.error(f"Error fetching apps daily pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/chart/daily-pattern", response_model=List[DailyPatternPoint])
async def get_users_daily_pattern():
    """Total user requests by day of week — all time."""
    try:
        async with timescale_db.pool.acquire() as conn:
            rows = await conn.fetch(_CHART_USERS_QUERY)
        return _format_daily_pattern(rows)
    except Exception as e:
        logger.error(f"Error fetching users daily pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Cost by Model
# ============================================

class ModelCost(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float

@router.get("/cost-by-model", response_model=List[ModelCost])
async def get_cost_by_model(range: str = Query("all", pattern="^(1d|1w|1m|all)$")):
    """Per-model token usage and estimated cost."""
    f = _RANGE_FILTERS[range]
    query = f"""
    SELECT resolved_model,
           COALESCE(SUM(input_tokens), 0)  AS input_tokens,
           COALESCE(SUM(output_tokens), 0) AS output_tokens
    FROM llm_request_logs
    WHERE resolved_model IS NOT NULL AND {f}
    GROUP BY resolved_model
    ORDER BY (COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)) DESC;
    """
    try:
        async with timescale_db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        results = []
        for row in rows:
            model = row["resolved_model"]
            rates = COST_PER_1K_TOKENS.get(model, DEFAULT_COST)
            in_tok = int(row["input_tokens"])
            out_tok = int(row["output_tokens"])
            cost = (in_tok / 1000 * rates["input"]) + (out_tok / 1000 * rates["output"])
            results.append(ModelCost(model=model, input_tokens=in_tok, output_tokens=out_tok, estimated_cost_usd=round(cost, 4)))
        return results
    except Exception as e:
        logger.error(f"Error fetching cost by model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Hourly throughput chart
# ============================================

class HourlyPoint(BaseModel):
    hour: str
    requests: int
    avg_latency_ms: float

@router.get("/chart/hourly-throughput", response_model=List[HourlyPoint])
async def get_hourly_throughput():
    """Requests per hour for the last 24 hours."""
    query = f"""
    SELECT time_bucket('1 hour', timestamp) AS bucket,
           COUNT(*) AS requests,
           COALESCE(ROUND(AVG(latency_ms) FILTER (WHERE status = 'success')), 0) AS avg_latency_ms
    FROM llm_request_logs
    WHERE timestamp > NOW() - INTERVAL '24 hours'
    GROUP BY bucket
    ORDER BY bucket;
    """
    try:
        async with timescale_db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [
            HourlyPoint(
                hour=row["bucket"].strftime("%H:%M"),
                requests=row["requests"],
                avg_latency_ms=float(row["avg_latency_ms"] or 0),
            )
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Error fetching hourly throughput: {e}")
        raise HTTPException(status_code=500, detail=str(e))

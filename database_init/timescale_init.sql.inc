-- ============================================
-- TimescaleDB Database Schema
-- Request Logs & Metrics
-- LLM Gateway v2.3.0
-- ============================================
-- Run once before first deploy:
--   psql -h <host> -U <user> -d <database> -f timescale_init.sql
-- Safe to re-run — all statements use IF NOT EXISTS / OR REPLACE.
-- ============================================


-- ============================================
-- 1. EXTENSION
-- ============================================

CREATE EXTENSION IF NOT EXISTS timescaledb;


-- ============================================
-- 2. REQUEST LOGS TABLE (TimescaleDB Hypertable)
-- ============================================

CREATE TABLE IF NOT EXISTS public.llm_request_logs (
    -- Partition key — single source of truth for request time
    timestamp           TIMESTAMPTZ     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_id          UUID            NOT NULL,

    -- Entity identity — exactly one of user_net_id / app_name is populated
    -- entity_type drives which column is set; the other is NULL
    entity_type         TEXT            NOT NULL,   -- 'user' or 'app'
    user_net_id         VARCHAR(255),               -- set if entity_type = 'user'
    app_name            VARCHAR(255),               -- set if entity_type = 'app'

    -- Model tracking
    -- requested_model: what the client passed (may be 'auto', a model string, or NULL)
    -- resolved_model:  the actual model sent to upstream LLM (set by processor)
    requested_model     TEXT,
    resolved_model      TEXT,

    -- Request details
    llm_type            TEXT            NOT NULL,   -- 'general' or 'coding'
    input_messages      JSONB           NOT NULL,
    input_tokens        INTEGER,
    temperature         REAL,
    max_tokens          INTEGER,

    -- Processing & caching
    queue_time_ms           INTEGER,                -- time spent waiting in Redis queue
    semantic_cache_enabled  BOOLEAN DEFAULT TRUE,   -- does this entity have caching enabled?
    cache_hit               BOOLEAN DEFAULT FALSE,  -- was response served from semantic cache?

    -- Output
    reasoning_content   TEXT,                       -- for reasoning models (o1, etc)
    response_content    TEXT,                       -- actual user-facing response
    output_tokens       INTEGER,
    total_tokens        INTEGER,

    -- Performance
    latency_ms          INTEGER,                    -- total end-to-end latency
    llm_latency_ms      INTEGER,                    -- time spent in the upstream LLM call only

    -- Status
    status              TEXT    DEFAULT 'pending',  -- 'pending', 'success', 'error', 'timeout', 'connection_failed'
    error_message       TEXT,

    -- Metadata
    upstream_url        TEXT,

    -- Primary key must include timestamp for TimescaleDB partitioning
    PRIMARY KEY (timestamp, request_id)
);

-- Convert to TimescaleDB hypertable (partitioned by timestamp)
SELECT create_hypertable('llm_request_logs', 'timestamp', if_not_exists => TRUE);


-- ============================================
-- 3. INDEXES
-- ============================================

CREATE INDEX IF NOT EXISTS idx_logs_request_id
    ON public.llm_request_logs(request_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_logs_user_net_id
    ON public.llm_request_logs(user_net_id, timestamp DESC)
    WHERE user_net_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_logs_app_name
    ON public.llm_request_logs(app_name, timestamp DESC)
    WHERE app_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_logs_entity_type
    ON public.llm_request_logs(entity_type, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_logs_llm_type
    ON public.llm_request_logs(llm_type, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_logs_status
    ON public.llm_request_logs(status, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_logs_cache_hit
    ON public.llm_request_logs(cache_hit, timestamp DESC)
    WHERE cache_hit = TRUE;

CREATE INDEX IF NOT EXISTS idx_logs_resolved_model
    ON public.llm_request_logs(resolved_model, timestamp DESC)
    WHERE resolved_model IS NOT NULL;


-- ============================================
-- 4. CONTINUOUS AGGREGATES
-- ============================================

-- Hourly aggregates per app
CREATE MATERIALIZED VIEW IF NOT EXISTS llm_requests_hourly_apps
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp)                                        AS bucket,
    app_name,
    llm_type,
    COUNT(*)                                                                AS total_requests,
    COUNT(*) FILTER (WHERE status = 'success')                              AS successful_requests,
    COUNT(*) FILTER (WHERE status = 'error')                                AS failed_requests,
    COUNT(*) FILTER (WHERE status = 'timeout')                              AS timeout_requests,
    COUNT(*) FILTER (WHERE status = 'connection_failed')                    AS connection_failed_requests,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)                                AS cached_requests,
    AVG(latency_ms)     FILTER (WHERE status = 'success')                   AS avg_latency_ms,
    AVG(llm_latency_ms) FILTER (WHERE status = 'success')                   AS avg_llm_latency_ms,
    AVG(queue_time_ms)  FILTER (WHERE status = 'success')                   AS avg_queue_time_ms,
    SUM(input_tokens)                                                       AS total_input_tokens,
    SUM(output_tokens)                                                      AS total_output_tokens,
    SUM(total_tokens)                                                       AS total_tokens,
    MIN(timestamp)                                                          AS first_request,
    MAX(timestamp)                                                          AS last_request
FROM llm_request_logs
WHERE entity_type = 'app' AND app_name IS NOT NULL
GROUP BY bucket, app_name, llm_type
WITH NO DATA;

-- Hourly aggregates per user
CREATE MATERIALIZED VIEW IF NOT EXISTS llm_requests_hourly_users
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp)                                        AS bucket,
    user_net_id,
    llm_type,
    COUNT(*)                                                                AS total_requests,
    COUNT(*) FILTER (WHERE status = 'success')                              AS successful_requests,
    COUNT(*) FILTER (WHERE status = 'error')                                AS failed_requests,
    COUNT(*) FILTER (WHERE status = 'timeout')                              AS timeout_requests,
    COUNT(*) FILTER (WHERE status = 'connection_failed')                    AS connection_failed_requests,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)                                AS cached_requests,
    AVG(latency_ms)     FILTER (WHERE status = 'success')                   AS avg_latency_ms,
    AVG(llm_latency_ms) FILTER (WHERE status = 'success')                   AS avg_llm_latency_ms,
    AVG(queue_time_ms)  FILTER (WHERE status = 'success')                   AS avg_queue_time_ms,
    SUM(input_tokens)                                                       AS total_input_tokens,
    SUM(output_tokens)                                                      AS total_output_tokens,
    SUM(total_tokens)                                                       AS total_tokens,
    MIN(timestamp)                                                          AS first_request,
    MAX(timestamp)                                                          AS last_request
FROM llm_request_logs
WHERE entity_type = 'user' AND user_net_id IS NOT NULL
GROUP BY bucket, user_net_id, llm_type
WITH NO DATA;

-- Daily aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS llm_requests_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', timestamp)                                                             AS bucket,
    llm_type,
    entity_type,
    COUNT(*)                                                                                    AS total_requests,
    COUNT(*) FILTER (WHERE status = 'success')                                                  AS successful_requests,
    COUNT(*) FILTER (WHERE status = 'error')                                                    AS failed_requests,
    COUNT(*) FILTER (WHERE status = 'timeout')                                                  AS timeout_requests,
    COUNT(*) FILTER (WHERE status = 'connection_failed')                                        AS connection_failed_requests,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)                                                    AS cached_requests,
    AVG(latency_ms)                                         FILTER (WHERE status = 'success')   AS avg_latency_ms,
    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'success') AS median_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'success') AS p95_latency_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status = 'success') AS p99_latency_ms,
    SUM(total_tokens)                                                                           AS total_tokens,
    COUNT(DISTINCT CASE WHEN entity_type = 'user' THEN user_net_id END)                        AS unique_users,
    COUNT(DISTINCT CASE WHEN entity_type = 'app'  THEN app_name    END)                        AS unique_apps
FROM llm_request_logs
GROUP BY bucket, llm_type, entity_type
WITH NO DATA;


-- ============================================
-- 5. CONTINUOUS AGGREGATE REFRESH POLICIES
-- ============================================

SELECT add_continuous_aggregate_policy('llm_requests_hourly_apps',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE
);

SELECT add_continuous_aggregate_policy('llm_requests_hourly_users',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE
);

SELECT add_continuous_aggregate_policy('llm_requests_daily',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists     => TRUE
);


-- ============================================
-- 6. COMPRESSION POLICY
-- Compress chunks older than 7 days to reduce storage.
-- ============================================

ALTER TABLE llm_request_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'entity_type, llm_type',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('llm_request_logs', compress_after => INTERVAL '7 days', if_not_exists => TRUE);


-- ============================================
-- 7. RETENTION POLICY
-- Auto-delete raw request logs older than 90 days.
-- Continuous aggregates are retained indefinitely.
-- ============================================

SELECT add_retention_policy('llm_request_logs', INTERVAL '90 days', if_not_exists => TRUE);


-- ============================================
-- 8. HELPER VIEWS
-- ============================================

-- Recent errors (last 24 hours)
CREATE OR REPLACE VIEW recent_errors AS
SELECT
    request_id,
    entity_type,
    user_net_id,
    app_name,
    llm_type,
    requested_model,
    resolved_model,
    status,
    error_message,
    queue_time_ms,
    timestamp
FROM llm_request_logs
WHERE status IN ('error', 'timeout', 'connection_failed')
  AND timestamp > NOW() - INTERVAL '24 hours'
ORDER BY timestamp DESC
LIMIT 100;

-- Performance summary (last hour)
CREATE OR REPLACE VIEW performance_summary AS
SELECT
    llm_type,
    entity_type,
    resolved_model,
    COUNT(*)                                                AS total_requests,
    COUNT(*) FILTER (WHERE status = 'success')              AS successful,
    COUNT(*) FILTER (WHERE status = 'error')                AS errors,
    COUNT(*) FILTER (WHERE status = 'timeout')              AS timeouts,
    COUNT(*) FILTER (WHERE status = 'connection_failed')    AS connection_failures,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)                AS cached,
    ROUND(AVG(latency_ms)::numeric, 2)                      AS avg_latency_ms,
    ROUND(AVG(llm_latency_ms)::numeric, 2)                  AS avg_llm_latency_ms,
    ROUND(AVG(queue_time_ms)::numeric, 2)                   AS avg_queue_ms,
    SUM(input_tokens)                                       AS total_input_tokens,
    SUM(output_tokens)                                      AS total_output_tokens,
    SUM(total_tokens)                                       AS total_tokens
FROM llm_request_logs
WHERE timestamp > NOW() - INTERVAL '1 hour'
GROUP BY llm_type, entity_type, resolved_model;

-- Top apps by request count (last 24 hours)
CREATE OR REPLACE VIEW top_apps_24h AS
SELECT
    app_name,
    COUNT(*)                                            AS request_count,
    COUNT(*) FILTER (WHERE status = 'success')          AS successful_requests,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)            AS cached_requests,
    SUM(input_tokens)                                   AS total_input_tokens,
    SUM(output_tokens)                                  AS total_output_tokens,
    SUM(total_tokens)                                   AS total_tokens,
    ROUND(AVG(latency_ms)::numeric, 2)                  AS avg_latency_ms
FROM llm_request_logs
WHERE entity_type = 'app'
  AND app_name IS NOT NULL
  AND timestamp > NOW() - INTERVAL '24 hours'
GROUP BY app_name
ORDER BY request_count DESC
LIMIT 20;

-- Top users by request count (last 24 hours)
CREATE OR REPLACE VIEW top_users_24h AS
SELECT
    user_net_id,
    COUNT(*)                                            AS request_count,
    COUNT(*) FILTER (WHERE status = 'success')          AS successful_requests,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)            AS cached_requests,
    SUM(input_tokens)                                   AS total_input_tokens,
    SUM(output_tokens)                                  AS total_output_tokens,
    SUM(total_tokens)                                   AS total_tokens,
    ROUND(AVG(latency_ms)::numeric, 2)                  AS avg_latency_ms
FROM llm_request_logs
WHERE entity_type = 'user'
  AND user_net_id IS NOT NULL
  AND timestamp > NOW() - INTERVAL '24 hours'
GROUP BY user_net_id
ORDER BY request_count DESC
LIMIT 20;

-- Request rate per minute (last hour)
CREATE OR REPLACE VIEW request_rate_per_minute AS
SELECT
    time_bucket('1 minute', timestamp) AS minute,
    llm_type,
    entity_type,
    COUNT(*) AS requests_per_minute
FROM llm_request_logs
WHERE timestamp > NOW() - INTERVAL '1 hour'
GROUP BY minute, llm_type, entity_type
ORDER BY minute DESC;

-- Semantic cache performance (last 24 hours)
CREATE OR REPLACE VIEW semantic_cache_performance AS
SELECT
    entity_type,
    llm_type,
    COUNT(*) FILTER (WHERE semantic_cache_enabled = TRUE)   AS total_requests_with_cache,
    COUNT(*) FILTER (WHERE cache_hit = TRUE)                AS cache_hits,
    ROUND(
        COUNT(*) FILTER (WHERE cache_hit = TRUE)::numeric /
        NULLIF(COUNT(*) FILTER (WHERE semantic_cache_enabled = TRUE), 0) * 100,
        2
    )                                                       AS cache_hit_percentage,
    ROUND(AVG(input_tokens)  FILTER (WHERE semantic_cache_enabled = TRUE)::numeric, 0) AS avg_input_tokens,
    ROUND(AVG(output_tokens) FILTER (WHERE semantic_cache_enabled = TRUE)::numeric, 0) AS avg_output_tokens
FROM llm_request_logs
WHERE timestamp > NOW() - INTERVAL '24 hours'
GROUP BY entity_type, llm_type;


-- ============================================
-- 8. COMMENTS
-- ============================================

COMMENT ON TABLE  public.llm_request_logs IS 'Hypertable storing all LLM request logs with time-series optimization via TimescaleDB';
COMMENT ON COLUMN public.llm_request_logs.entity_type           IS 'Type of entity making the request: user or app';
COMMENT ON COLUMN public.llm_request_logs.user_net_id           IS 'net_id if entity_type=user, otherwise NULL';
COMMENT ON COLUMN public.llm_request_logs.app_name              IS 'app_name if entity_type=app, otherwise NULL';
COMMENT ON COLUMN public.llm_request_logs.requested_model       IS 'Model requested by client — may be auto, a specific model string, or NULL';
COMMENT ON COLUMN public.llm_request_logs.resolved_model        IS 'Actual model used for the upstream LLM call — set by the processor';
COMMENT ON COLUMN public.llm_request_logs.semantic_cache_enabled IS 'Whether this entity has semantic caching enabled';
COMMENT ON COLUMN public.llm_request_logs.cache_hit             IS 'Whether this response was served from semantic cache';
COMMENT ON COLUMN public.llm_request_logs.status                IS 'Request status: pending, success, error, timeout, connection_failed';
COMMENT ON COLUMN public.llm_request_logs.queue_time_ms         IS 'Time spent waiting in Redis queue before processor picked up the request';
COMMENT ON COLUMN public.llm_request_logs.llm_latency_ms        IS 'Time spent in the actual upstream LLM API call';
COMMENT ON COLUMN public.llm_request_logs.latency_ms            IS 'Total end-to-end latency from request received to response returned';
# Technical Specification: Echo Log — LLM Gateway

**Status:** Approved  
**Lead Engineer:** Abhinay Yarlagadda  
**Related Product Brief:** Echo Log README

---

## 1. Executive Technical Summary

Echo Log is a production LLM gateway deployed at DSRS (Gies College of Business, UIUC) that provides a single, authenticated, observable entry point to multiple self-hosted vLLM inference backends. It is OpenAI SDK-compatible, meaning existing code using the OpenAI Python client requires only a `base_url` and `api_key` change to route through the gateway.

**Primary Tech Stack:** FastAPI, asyncpg, Redis, PostgreSQL (CNPG), TimescaleDB, Kubernetes (K3s), Traefik, ArgoCD, Vault (External Secrets), Harbor

**Key Goals:**

- Centralized auth and observability for all LLM usage across DSRS platforms
- Zero-overhead streaming (SSE passthrough via Redis delivery bus)
- Sub-millisecond auth on repeat requests via in-memory bcrypt cache
- Live GPU health visibility without exposing vLLM internals externally
- Resilient startup — retries all DB connections with exponential backoff rather than crashing into Kubernetes restart loops

---

## 2. System Architecture

### 2.1 High-Level Diagram

```
Client (OpenAI SDK / curl)
    ↓  Authorization: Bearer dsrs-<key>
FastAPI Gateway — echo.dsrs.illinois.edu
    ├── Auth: bcrypt cache → PostgreSQL fallback
    ├── Log: TimescaleDB (pending row)
    └── Enqueue: Redis llm_queue:{general|coding}
            ↓
    LLMProcessor (asyncio background task)
    ├── General: llm.dsrs.illinois.edu (vLLM, Llama 4 / gpt-oss-120b)
    └── Coding:  llm-code.dsrs.illinois.edu (vLLM, Gemma / Qwen)
            ↓
    Redis delivery bus (response:{request_id})
    └── Gateway polls → SSE stream or JSON response

Background Tasks (always running, per pod)
    ├── GPU Scraper — general  →  llm.ai-models.svc.cluster.local:8000/metrics
    └── GPU Scraper — coding   →  llm-code.ai-models.svc.cluster.local:8000/metrics
```

**Client:** OpenAI Python SDK, curl, or any HTTP client  
**API Gateway:** Traefik (Kubernetes ingress), FastAPI (application layer)  
**Services:** Auth cache, LLM queue processors (general + coding), GPU metric scrapers, TimescaleDB logger, model manager

### 2.2 Infrastructure

**Hosting:** Kubernetes (K3s) cluster at NCSA / UIUC  
**Namespaces:**

- `echo-log` — gateway backend, frontend, TimescaleDB, Redis
- `ai-models` — vLLM inference containers (llm, llm-code)
- `pg` — CNPG PostgreSQL cluster (dsrs-ssd-new)
- `farid` — shared Redis instance

**Container Registry:** `hub.ncsa.illinois.edu/gies-dsrs/`  
**CI/CD:** Git push → Harbor image build → Keel watches `latest` tag → automatic pod restart  
**GitOps:** ArgoCD (`dsrs-naboo` app) syncs Helm chart changes  
**Secrets:** Vault via External Secrets Operator — `echo-log-env` secret synced from `cluster/apps/echo-log` path, refreshed every 1 hour  
**Storage:**

- PostgreSQL (CNPG) — SSD-backed PVC (`dsrs-ssd-new`)
- TimescaleDB — NFS-backed PVC (`nfs-taiga`) ⚠️ migration to SSD planned
- Redis — in-memory, no persistence required

---

## 3. Data Model (Database Design)

### 3.1 PostgreSQL Schema — User & App Management

| Entity | Field | Type | Notes |
|:---|:---|:---|:---|
| **users** | id | SERIAL | Primary Key |
| | first_name | VARCHAR(100) | |
| | last_name | VARCHAR(100) | |
| | net_id | VARCHAR(255) | Unique, Indexed |
| | api_key_hash | VARCHAR(255) | bcrypt hash only — plaintext never stored; NULL = no key |
| | is_active | BOOLEAN | Default TRUE |
| | is_admin | BOOLEAN | Default FALSE |
| | created_at | TIMESTAMPTZ | |
| | updated_at | TIMESTAMPTZ | Auto-updated via trigger |
| **apps** | id | SERIAL | Primary Key |
| | app_name | VARCHAR(255) | Unique, Indexed |
| | api_key_hash | VARCHAR(255) | bcrypt hash only — NULL = no key |
| | description | TEXT | |
| | created_by | VARCHAR(255) | net_id of creating admin |
| | is_active | BOOLEAN | Default TRUE |
| | semantic_cache_enabled | BOOLEAN | Default TRUE |
| | created_at | TIMESTAMPTZ | |
| | updated_at | TIMESTAMPTZ | Auto-updated via trigger |

### 3.2 TimescaleDB Schema — Request Logs & Metrics

| Entity | Field | Type | Notes |
|:---|:---|:---|:---|
| **llm_request_logs** | timestamp | TIMESTAMPTZ | Hypertable partition key |
| | request_id | UUID | |
| | entity_type | TEXT | `'user'` or `'app'` |
| | user_net_id | TEXT | Set if entity_type=user |
| | app_name | TEXT | Set if entity_type=app |
| | llm_type | TEXT | `'general'` or `'coding'` |
| | requested_model | TEXT | What the client passed (may be `'auto'`) |
| | resolved_model | TEXT | Actual model sent upstream |
| | input_messages | JSONB | Full messages array |
| | temperature | FLOAT | |
| | max_tokens | INT | |
| | semantic_cache_enabled | BOOLEAN | |
| | status | TEXT | `pending` → `success` / `error` / `timeout` |
| | cache_hit | BOOLEAN | |
| | response_content | TEXT | |
| | reasoning_content | TEXT | For reasoning models |
| | input_tokens | INT | |
| | output_tokens | INT | |
| | total_tokens | INT | |
| | latency_ms | INT | End-to-end |
| | llm_latency_ms | INT | Upstream LLM only |
| | queue_time_ms | INT | Time spent in Redis queue |
| | error_message | TEXT | Set on failure |
| | upstream_url | TEXT | |

**Continuous Aggregates:**

- `llm_requests_hourly_apps` — per app per hour
- `llm_requests_hourly_users` — per user per hour
- `llm_requests_daily` — daily with p50/p95/p99 latencies

### 3.3 Redis Key Schema

| Key Pattern | TTL | Purpose |
|:---|:---|:---|
| `llm_queue:{general\|coding}` | none | FIFO request queue (RPUSH/LPOP) |
| `response:{request_id}` | 360s | Non-streaming response delivery bus (GETDEL — consumed once) |
| `stream:{request_id}:{chunk_index}` | 360s | Streaming chunk delivery |

---

## 4. API Design & Interfaces

**Base URLs:**

- General LLM: `https://echo.dsrs.illinois.edu/general/v1`
- Coding LLM: `https://echo.dsrs.illinois.edu/coding/v1`
- Admin/Metrics: `https://echo.dsrs.illinois.edu`

**Authentication:** `Authorization: Bearer dsrs-<32 base62 chars>`

### Key Endpoints

**Public (no auth)**

- `GET /health` — system health including DB, Redis, LLM backends, queue depths
- `GET /gpu-metrics` — live KV cache % and active requests per vLLM backend
- `GET /metrics/all?range={1d|1w|1m|all}` — aggregate scorecard
- `GET /metrics/apps?range=...` — app-scoped scorecard
- `GET /metrics/users?range=...` — user-scoped scorecard
- `GET /metrics/*/chart/daily-pattern` — traffic by day of week (all time)
- `POST /keys/self-generate` — regular user issues own key (`{net_id}`)

**Bootstrap key required**

- `POST /admin/keys/self-bootstrap` — admin issues own key (`{net_id}`)

**Any valid API key**

- `POST /{general|coding}/v1/chat/completions` — OpenAI-compatible chat completions
- `GET /{general|coding}/v1/models` — list upstream models (served from cache)

**Admin key required**

- `POST /admin/users/create`, `GET /admin/users/list`
- `POST /admin/apps/create`, `GET /admin/apps/list`
- `POST /admin/keys/user/generate`, `POST /admin/keys/app/generate`, `POST /admin/keys/null`
- `POST /admin/models/refresh`, `GET /admin/models/cache`
- `GET /debug/recent-logs`

---

## 5. Architecture Decision Records (ADR)

### ADR 1: Redis as Queue + Delivery Bus (not direct async)

**Decision:** All LLM requests are enqueued to Redis and processed by background `LLMProcessor` tasks. The gateway polls Redis for the result rather than awaiting the upstream directly.

**Reasoning:** Decouples request acceptance from LLM processing. Allows concurrent request limiting per LLM type (`GENERAL_LLM_CONCURRENT_LIMIT`) without blocking the FastAPI event loop. Enables streaming via Redis chunk keys without holding an open HTTP connection to the upstream for the full duration.

### ADR 2: bcrypt + In-Memory Cache for Auth

**Decision:** API keys are bcrypt-hashed at rest. A SHA-256-keyed in-memory dict caches verified keys for 5 minutes per pod.

**Reasoning:** bcrypt at work factor 12 costs ~150ms per verification — unacceptable on every LLM request. The cache reduces this to ~1ms on repeat requests within the TTL window. SHA-256 is used as the cache key so the plaintext secret never sits in the dict. Key nulling synchronously evicts the cache entry so revocation is immediate rather than waiting for TTL expiry.

### ADR 3: Two Separate LLM Queues (general + coding)

**Decision:** General and coding LLMs each have their own Redis queue and `LLMProcessor` instance with independent concurrency limits.

**Reasoning:** Coding requests (longer context, slower models) should not block general requests. Independent queues allow different concurrency limits and allow the gateway to route by request type without cross-queue interference.

### ADR 4: Static SQL Query Map for Metrics

**Decision:** All metric queries are pre-built as string constants at module load time in `api/metrics.py`. No SQL is constructed at request time.

**Reasoning:** The time filter (`WHERE timestamp >= ...`) is a structural SQL fragment, not a value, so it cannot be safely parameterised with `$1`. Pre-building all variants at startup eliminates any runtime string construction and makes the SQL fully auditable. User input only controls which pre-built query is selected (validated against a fixed enum).

### ADR 5: GPU Metrics via Background Scraper (not proxy)

**Decision:** A background asyncio task scrapes each vLLM `/metrics` endpoint every 1 second and writes into an in-memory state object. The `/gpu-metrics` endpoint reads from that object synchronously.

**Reasoning:** The vLLM Prometheus `/metrics` endpoint is only reachable internally (not through Traefik). Proxying the full Prometheus text wall to the frontend would be wasteful (~50KB per poll). The scraper extracts only two metrics (`kv_cache_usage_perc`, `num_requests_running`) and serves a tiny JSON object. The frontend always gets an instant response even when vLLM is temporarily unreachable.

### ADR 6: Exponential Backoff on DB Connection at Startup

**Decision:** All three DB connections (PostgreSQL, TimescaleDB, Redis) retry up to 10 times with exponential backoff (3s → 6s → ... capped at 60s) before giving up.

**Reasoning:** Without this, a transient TimescaleDB I/O stall (caused by NFS fsync latency) causes the gateway lifespan to fail, Kubernetes restarts the pod immediately, and the pod burns through CrashLoopBackOff budget in minutes. Retrying inside the lifespan keeps the pod alive while waiting for the dependency, at the cost of a delayed startup.

---

## 6. Security & Performance

### Security

- **Plaintext keys never stored** — only bcrypt hashes in DB; plaintext shown once at generation
- **SHA-256 cache keys** — raw API key never used as a dict key in memory
- **Constant-time bootstrap comparison** — `hmac.compare_digest` prevents timing attacks on the bootstrap secret
- **Static SQL** — no user input ever interpolated into query strings; all time filters are pre-built constants
- **Admin scope enforcement** — apps can never be admins; admins cannot generate keys for other admins
- **Immediate cache eviction on key null** — revoked keys stop working instantly, not after TTL
- **CORS validator** — startup fails with a clear error if `cors_allow_credentials=True` is combined with `cors_origins=["*"]`
- **TODO:** Move `BOOTSTRAP_API_KEY` from ConfigMap to Vault-sourced secret

### Performance

- **Auth cache** — bcrypt cost (~150ms) paid once per 5-minute TTL window per key per pod; subsequent requests ~1ms
- **Model cache** — upstream model detection cached 60 minutes; `POST /admin/models/refresh` for immediate invalidation
- **Redis GETDEL** — response delivery keys consumed atomically in a single operation; no ghost keys
- **asyncpg connection pool** — `max_inactive_connection_lifetime=300` recycles idle connections before server-side closure; prevents stale connection errors after idle periods
- **Pre-built metric queries** — zero SQL construction at request time; all query strings are module-level constants
- **GPU scraper** — 1s poll interval, 2s httpx timeout; state object written in background, read synchronously on request; never blocks the request path

---

## 7. Critical Risks & Unknowns

- **TimescaleDB on NFS storage (`nfs-taiga`)** — NFS has severe fsync latency for Postgres write workloads. Checkpoint sync times of 20 minutes have been observed, causing the Postgres process to become unresponsive to new connections during I/O stalls. Mitigation: `synchronous_commit = off` (must be re-applied after each pod restart). Permanent fix: migrate PVC to SSD-backed storage class. This is the highest-priority infrastructure risk.

- **Single-pod auth cache** — the in-memory bcrypt cache is process-local. If multiple gateway replicas are running, a key nulled via one pod's cache eviction will still be valid on other pods for up to 5 minutes. Currently running as a single replica; this becomes a risk at scale.

- **TimescaleDB is not CNPG-managed** — unlike PostgreSQL, TimescaleDB is a plain Deployment with a PVC. There is no automatic failover, PITR backup, or replica. A pod crash loses any writes not yet fsynced to the PVC.

- **Semantic cache integration incomplete** — the `cache.check()` and `cache.add()` hooks are in place in `task_queue/processor.py` (commented out) but the backing library is not yet integrated. `semantic_cache_enabled` flag is tracked per-entity and logged to TimescaleDB but has no effect on response serving.

- **`latest` image tag in production** — the deployment uses `hub.ncsa.illinois.edu/gies-dsrs/echo-log-backend:latest`. This means any pushed image immediately becomes the candidate for Keel to deploy. A bad push goes straight to production. Consider pinning to SHA or a versioned tag with a staging step.

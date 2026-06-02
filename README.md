# EchoLog

## Problem

Organizations deploying open-source LLMs on shared on-premise A100 GPUs need centralized access control and observability to prevent any single team from monopolizing GPU inference slots. EchoLog is an OpenAI-compatible API gateway that enforces per-account rate limits, queues excess requests with FIFO fairness, and streams every request/response into a time-series analytics dashboard — all without modifying client code beyond swapping the base URL.

## Architecture

```
                         ┌─────────────────────────────────────────────────────────────┐
                         │                      REQUEST PATH                           │
                         │                                                             │
  Client (OpenAI SDK)    │   Traefik v3.2        Gunicorn / Uvicorn workers            │
  ──────────────────►    │   ┌───────────┐       ┌──────────────────────────┐          │
       :80               │   │  Ingress  │──────►│  FastAPI  (2 replicas,   │          │
                         │   │  L7 LB +  │       │  4 workers each)         │          │
                         │   │  health   │       └──────┬───────┬───────┬───┘          │
                         │   └───────────┘              │       │       │              │
                         └──────────────────────────────┼───────┼───────┼──────────────┘
                                                        │       │       │
                ┌───────────────────────────────────────┘       │       └──────────────────┐
                │                                               │                          │
                ▼                                               ▼                          ▼
  ┌──────────────────────────────┐            ┌─────────────────────────┐    ┌──────────────────────┐
  │          Redis               │            │  Celery worker + Beat   │    │     PostgreSQL       │
  │                              │            │                         │    │     (llm_gateway)    │
  │  Per-account rate limiting:  │            │  buffer_log task:       │    │                      │
  │   active:{entity}  — SADD   │            │   append to buffer      │    │  users table         │
  │     + SETEX 120s TTL keys   │            │                         │    │  apps table          │
  │   queued:{entity}  — RPUSH/ │            │  flush_log_buffer:      │    │  bcrypt-12 key hashes│
  │     LPOP FIFO               │            │   every 5s (Beat) OR    │    │  SHA-256 auth cache  │
  │                              │            │   100 records (buffer)  │    │    (5 min TTL)       │
  │  Global processor queue:    │            │           │              │    └──────────────────────┘
  │   llm_queue:{type} — RPUSH/ │            │           ▼              │
  │     LPOP FIFO               │            │  ┌────────────────────┐ │
  │                              │            │  │   TimescaleDB      │ │
  │  Response bus:              │            │  │   (llm_metrics)    │ │
  │   response:{id} — GET/DEL   │            │  │                    │ │
  │                              │            │  │  batch_insert()    │ │
  │  Stream chunks:             │            │  └────────┬───────────┘ │
  │   stream:{id}:{n} — SETEX   │            └───────────┼─────────────┘
  └──────────────────────────────┘                        │
                                                          ▼
                                           ┌──────────────────────────────┐
                                           │  Continuous Aggregates       │
                                           │                              │
                                           │  hourly per app/user         │
                                           │  daily with p50/p95/p99      │
                                           │  compression after 7 days    │
                                           │  retention: 90 days raw      │
                                           └──────────────┬───────────────┘
                                                          │
                                                          ▼
                                           ┌──────────────────────────────┐
                                           │  React + Recharts Dashboard  │
                                           │                              │
                                           │  /metrics/all?range=1d       │
                                           │  /metrics/chart/hourly-...   │
                                           │  /metrics/cost-by-model      │
                                           └──────────────────────────────┘


  ┌─────────────────────────────────────────────────────────────┐
  │                  CANCELLATION FLOW                          │
  │                                                             │
  │  POST /api/v1/requests/{id}/cancel                         │
  │       │                                                     │
  │       ▼                                                     │
  │  Redis SETEX cancel:{id} 60s "1"                           │
  │       │                                                     │
  │       ▼                                                     │
  │  LLMProcessor._handle_streaming_request polls:             │
  │    if await redis_client.is_cancelled(request_id):         │
  │        → write final stream chunk, break                   │
  └─────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Technology | Role | Key Design Decision |
|---|---|---|
| FastAPI + Gunicorn | API server | Uvicorn worker class, 4 workers per replica; async handlers for non-blocking Redis/DB I/O |
| Traefik v3.2 | Ingress / load balancer | L7 routing with active health checks (`/health` every 10s); Kubernetes IngressRoute with 100 req/s rate limit middleware |
| Redis 7 | Queue + rate-limit state | SADD sets for active slots with per-member TTL via auxiliary SETEX keys; RPUSH/LPOP FIFO for per-account and global processor queues |
| Celery + Beat | Async log buffering | `buffer_log` task appends to in-memory list; Beat triggers `flush_log_buffer` every 5s; buffer also flushes at 100 records |
| TimescaleDB (PG 16) | Time-series request logs | Hypertable partitioned by timestamp; continuous aggregates (hourly per app/user, daily with p50/p95/p99); compression after 7 days; 90-day retention |
| PostgreSQL | Auth database | Stores users, apps, and bcrypt-12 hashed API keys; separate from TimescaleDB so compression policies don't affect auth tables |
| bcrypt (work factor 12) | API key hashing | ~100-200ms per verify; SHA-256 in-memory auth cache (5-min TTL) absorbs cost on repeated requests |
| React 18 + Recharts | Analytics dashboard | Fetches `/metrics/*` endpoints; scorecard cards, hourly bar/line charts, daily pattern, cost-by-model pie chart |
| Docker Compose | Local orchestration | 6 services: Traefik, FastAPI (2 replicas), Celery worker, Celery Beat, Redis, TimescaleDB |
| Kubernetes | Production deployment | Separate Deployments for gateway (2 replicas), celery-worker, celery-beat; ConfigMap + Secret for env; Traefik IngressRoute |
| GitHub Actions | CI/CD | ruff lint → pytest with Redis service → Docker buildx multi-arch (amd64/arm64) push to Harbor registry |

## Key Design Decisions

**1. Redis SADD/TTL for active slots (vs INCR/DECR).** Each active request is tracked as a named member in a Redis set (`active:{entity_type}:{identifier}`), with a companion TTL key (`active_member:{request_id}`, 120s SETEX). This enables two things that simple INCR/DECR counters cannot: per-request cancellation (the cancel endpoint sets a `cancel:{id}` flag that the processor polls mid-stream, then releases the specific slot via SREM) and orphan cleanup (a background task scans active sets every 10 seconds, finds members whose TTL key has expired, removes them, and promotes the next queued request). With INCR/DECR, a crashed processor would permanently leak a counter, eventually starving the account.

**2. Celery batch insert (5s / 100 records).** Request logging is decoupled from the hot path by sending a `buffer_log` Celery task at enqueue time rather than writing to TimescaleDB inline. The Celery worker appends to an in-memory buffer; Celery Beat flushes every 5 seconds, or the buffer flushes itself when it hits 100 records — whichever comes first. This means a chat completion request never blocks on a database INSERT. The tradeoff is that the most recent 0-5 seconds of logs may be lost on a hard worker crash, which is acceptable for observability data.

**3. Two databases (PostgreSQL for auth, TimescaleDB for logs).** Auth data (users, apps, bcrypt hashes) lives in a plain PostgreSQL database (`llm_gateway`), while request logs live in a TimescaleDB hypertable (`llm_metrics`). This separation exists because TimescaleDB's compression policy (`compress_after => 7 days`, segmented by `entity_type, llm_type`) and retention policy (`drop_chunks after 90 days`) are designed for append-only time-series data and would be inappropriate for auth tables that require random-access reads and in-place updates. In the Docker Compose setup both databases run on the same TimescaleDB instance as separate logical databases; in production they can be split onto different hosts.

**4. Per-account rate limits enforced at enqueue time (not at LLM call time).** The `check_and_enqueue` method in `redis_client.py` checks whether the account has capacity (user: 1 active + 2 queued; app: 3 active + 6 queued) and rejects the request with HTTP 429 before it ever enters the global processor queue. If rate limits were checked later — at the point the processor dequeues and calls the upstream LLM — a single heavy user could fill the global queue and starve other accounts even though the LLM itself has capacity. Enforcing at enqueue time means the global `llm_queue:{type}` only contains requests that have already passed per-account admission control.

## How to Run

### Docker Compose (local)

```bash
cp .env.example .env
docker compose up --build
```

### Kubernetes

```bash
kubectl apply -f k8s/configmap.yml
kubectl apply -f k8s/deployment.yml
kubectl apply -f k8s/service.yml
kubectl apply -f k8s/ingressroute.yml
```

### Environment Variables

| Variable | Example Value | Description |
|---|---|---|
| `GENERAL_LLM_URL` | `http://host.docker.internal:11434/v1` | Upstream general LLM base URL |
| `GENERAL_LLM_API_KEY` | `sk-local` | API key for general LLM backend |
| `GENERAL_LLM_CONCURRENT_LIMIT` | `4` | Max concurrent general inference requests |
| `CODING_LLM_URL` | `http://host.docker.internal:11434/v1` | Upstream coding LLM base URL |
| `CODING_LLM_API_KEY` | `sk-local` | API key for coding LLM backend |
| `CODING_LLM_CONCURRENT_LIMIT` | `4` | Max concurrent coding inference requests |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_URL` | `redis://redis:6379/0` | Redis URL for Celery broker |
| `POSTGRES_HOST` | `timescaledb` | PostgreSQL hostname (auth database) |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `llm_gateway` | PostgreSQL database name |
| `POSTGRES_USER` | `admin` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `changeme` | PostgreSQL password |
| `TIMESCALE_HOST` | `timescaledb` | TimescaleDB hostname (logs database) |
| `TIMESCALE_PORT` | `5432` | TimescaleDB port |
| `TIMESCALE_DB` | `llm_metrics` | TimescaleDB database name |
| `TIMESCALE_USER` | `admin` | TimescaleDB user |
| `TIMESCALE_PASSWORD` | `changeme` | TimescaleDB password |
| `ENABLE_AUTH` | `false` | Enable API key authentication |
| `BOOTSTRAP_API_KEY` | `your-secret` | One-time bootstrap secret for initial admin key |

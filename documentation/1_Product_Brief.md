# Product Brief: Echo Log — LLM Gateway

**Status:** Approved  
**Last Updated:** May 2026  
**Authors:** Abhinay Yarlagadda, DSRS (Data Science Research Services), Gies College of Business, UIUC

---

## 1. Executive Summary

Echo Log is the centralized LLM gateway for DSRS, providing a single authenticated, observable, and rate-controlled entry point to all self-hosted AI inference infrastructure. It allows DSRS platforms and researchers to consume large language models through a standard OpenAI-compatible API without managing credentials, model endpoints, or infrastructure directly.

**Project Goal:** Provide a production-grade LLM gateway that abstracts away multiple vLLM backends behind a unified API, enforces role-based access control, logs all usage to a time-series database, and surfaces live infrastructure health — enabling DSRS to scale AI-powered tooling across platforms and researchers without exposing raw inference endpoints.

**Launch Date:** April 2026 (production)

---

## 2. Problem Statement & Opportunity

**The Problem:** DSRS operates multiple self-hosted vLLM inference servers (general and coding) across Kubernetes. Before Echo Log, each platform (Atlas, Blockchain Hub, i-Hire, etc.) connected directly to these servers with shared API keys, no usage logging, no rate control, and no visibility into who was using what or how much. A single misconfigured app could saturate GPU memory for all users.

**The Solution:** A FastAPI gateway that sits in front of all inference backends, validates per-entity API keys, queues requests through Redis to enforce concurrency limits, logs every request to TimescaleDB, and exposes live GPU health metrics — all behind a single `echo.dsrs.illinois.edu` endpoint that is OpenAI SDK-compatible out of the box.

**Value Proposition:**

- Researchers and platform teams get a stable, documented API they can use from day one with zero infrastructure knowledge
- DSRS administrators get full visibility into model usage by app and user, enabling capacity planning and cost attribution
- Security posture improves — raw vLLM endpoints are no longer exposed to application code; all access is mediated through bcrypt-hashed per-entity keys that can be revoked instantly
- Future capabilities (semantic caching, rate limiting, cost tracking) can be added at the gateway layer without changing any downstream application code

---

## 3. Target Audience (User Personas)

**The Platform Developer** (e.g., Atlas, i-Hire, Blockchain Hub engineer)

- Needs a drop-in OpenAI-compatible endpoint they can point their existing code at
- Does not want to manage model names, API keys per backend, or streaming infrastructure
- Needs their app to have its own key that can be rotated or revoked independently

**The Researcher / Analyst**

- Needs direct LLM access for notebooks, scripts, and experiments
- Has a net_id but no infrastructure access
- Needs self-service key generation without involving an admin for routine use

**The DSRS Administrator**

- Needs to provision and revoke access for users and apps
- Needs usage dashboards showing which apps/users are consuming the most tokens and when
- Needs to know if the GPU is under pressure before researchers start complaining about slow responses

**The Dashboard / Monitoring Consumer** (Echo Log frontend)

- Needs a live view of gateway health, request volume, latency, and GPU KV cache status
- Needs time-range filtering (today / this week / this month / all time)
- Needs no authentication — metrics are internal and non-sensitive

---

## 4. Functional Requirements (The "What")

### 4.1 Must Have (P0 — MVP, shipped)

- **OpenAI-compatible chat completions:** Any client using the OpenAI Python SDK can route through Echo Log by changing `base_url` and `api_key` only — no other code changes required.
- **Dual LLM backend support:** Separate queues and endpoints for general (`/general/v1`) and coding (`/coding/v1`) inference servers with independent concurrency limits.
- **Per-entity API keys:** Every user and app has its own `dsrs-` prefixed key. Keys are bcrypt-hashed at rest, shown exactly once, and can be nulled and regenerated independently.
- **Role-based access control:** Three tiers — regular user (self-service key), admin user (bootstrap key required), app (admin-issued key). Admins cannot generate keys for other admins.
- **Request logging:** Every request logged to TimescaleDB with entity, model, token counts, latency, cache hit status, and outcome.
- **Streaming support:** SSE streaming passthrough via Redis chunk delivery bus, OpenAI-compatible chunk format.
- **Auto model detection:** Clients can pass `model="auto"` and the gateway resolves the actual upstream model, cached for 60 minutes.
- **Metrics API:** Scorecard and daily pattern endpoints for all/app/user scopes with calendar-period range filtering (today, this week, this month, all time) in America/Chicago timezone.
- **Health endpoint:** Live status of all dependencies (PostgreSQL, TimescaleDB, Redis, both LLM backends) with queue depths and latencies.

### 4.2 Should Have (P1 — shipped)

- **In-memory auth cache:** bcrypt verification cost (~150ms) paid once per 5-minute TTL window. Immediate cache eviction on key null.
- **Live GPU metrics endpoint:** `/gpu-metrics` — KV cache usage percentage and active request count per vLLM backend, scraped every 1 second from internal ClusterIP, served instantly with staleness detection.
- **Admin CRUD:** Create/list users and apps, generate/null keys, force model cache refresh — all via authenticated admin endpoints.
- **Connection pool resilience:** Exponential backoff retry on all DB connections at startup; `max_inactive_connection_lifetime=300` to prevent stale connection errors after idle periods.
- **Bootstrap key for admin self-service:** First admin can generate their own key using a Vault-sourced secret without needing another admin to exist yet.

### 4.3 Could Have (P2 — pending)

- **Semantic caching:** Per-session similarity matching to serve repeated or near-duplicate queries from cache rather than hitting the LLM. Hooks are implemented (`cache.check()` / `cache.add()`) — library integration pending.
- **Per-entity rate limiting:** Token bucket or sliding window rate limits per user/app, configurable per entity.
- **Cost attribution dashboard:** Token usage translated to compute cost per entity per time period.
- **Webhook on key null:** Notify the entity (e.g., via Slack) when their key is revoked.

### 4.4 Won't Have (this version)

- Multi-tenant isolation at the network level — all entities share the same gateway pods and DB pool
- Per-request model selection for users (model routing is gateway-controlled)
- Fine-tuned model serving — gateway routes to base vLLM instances only
- Geographic redundancy or multi-cluster failover

---

## 5. Non-Functional Requirements

**Performance:**

- Auth cache hit latency: ~1ms (bcrypt cache hit) vs ~150ms (cache miss + DB verify)
- Streaming first-token latency: gateway overhead <5ms (Redis enqueue + poll)
- Metrics endpoints: <50ms (static SQL, pre-built at module load)
- GPU metrics: always instant (in-memory state object, zero network calls on request path)

**Security:**

- Plaintext API keys never stored — bcrypt hash only, work factor 12
- SHA-256 used as in-memory cache key so raw secret never sits in a dict
- Constant-time comparison (`hmac.compare_digest`) for bootstrap key verification
- All metric SQL pre-built as constants — no user input ever interpolated into query strings
- CORS misconfiguration caught at startup (`allow_credentials=True` + wildcard origin raises ValueError)
- Secrets sourced from Vault via External Secrets Operator, refreshed hourly

**Availability:**

- Startup retry logic (10 attempts, exponential backoff) prevents CrashLoopBackOff on transient DB unavailability
- GPU scraper failure surfaces as `"offline"` status — never crashes the gateway
- TimescaleDB write failures are logged and swallowed — a metrics write error never fails an LLM request

**Observability:**

- Every request has a UUID that appears in all log lines, TimescaleDB rows, and Redis keys
- Structured log format: `timestamp | level | service | message` with emoji prefixes for quick scanning
- `/health` endpoint covers all five dependencies with per-component latency

**Compatibility:**

- Fully OpenAI SDK-compatible — `openai.ChatCompletion.create()` works with only `base_url` and `api_key` changed
- SSE streaming format matches OpenAI chunk format exactly

---

## 6. Success Metrics (KPIs)

- **Adoption:** All DSRS production platforms (Atlas, Blockchain Hub, i-Hire, PitchBook) routing LLM traffic through Echo Log within 60 days of launch
- **Visibility:** 100% of LLM requests across DSRS infrastructure logged and queryable via the metrics API
- **Reliability:** Gateway pod restart count = 0 over any 7-day window in steady state (excluding intentional deploys)
- **Auth response time:** >95% of authenticated requests served from bcrypt cache (<5ms auth overhead)
- **GPU utilization awareness:** On-call team can determine KV cache pressure in <30 seconds via `/gpu-metrics` without needing kubectl access

---

## 7. Out of Scope

- **Frontend dashboard UI** — Echo Log exposes metrics APIs; the React/Vite dashboard at `echo-log.dsrs.illinois.edu` is a separate project consuming those APIs
- **vLLM infrastructure management** — Echo Log routes to vLLM containers but does not manage model loading, GPU allocation, or container lifecycle
- **Semantic cache library** — the integration hooks are built; the backing vector similarity library is out of scope for this phase
- **Billing or chargeback** — token usage is logged but cost attribution and financial reporting are out of scope
- **Multi-region or HA deployment** — single-cluster deployment; no cross-region failover
- **User-facing self-service portal** — key management is via API only; no web UI for end users in this version

---

## 8. Stakeholders & RACI

| Role | Name | Responsibility |
|:---|:---|:---|
| **Accountable** | DSRS Leadership | Final approval, infrastructure budget, Vault/secret management |
| **Responsible** | Abhinay Yarlagadda | Architecture, development, deployment, ongoing maintenance |
| **Consulted** | DSRS Platform Teams | Atlas, i-Hire, Blockchain Hub integration requirements and feedback |
| **Informed** | Researchers / End Users | API consumers — notified of breaking changes, key rotations, downtime |

---

## 9. Appendix / Related Links

- [Technical Specification](./TECHNICAL_SPEC.md) — architecture decisions, data model, API contract, risks
- [README](./README.md) — developer quickstart, endpoint reference, environment variables
- **Production:** <https://echo.dsrs.illinois.edu>
- **Dashboard:** <https://echo-log.dsrs.illinois.edu>
- **API Docs:** <https://echo.dsrs.illinois.edu/docs>
- **Harbor Registry:** `hub.ncsa.illinois.edu/gies-dsrs/echo-log-backend`
- **ArgoCD App:** `dsrs-naboo` → `echo-log` namespace

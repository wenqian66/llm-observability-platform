"""
EchoLog Gateway — Main Entry Point
OpenAI-compatible API with per-account rate limiting, queue management, and cancellation.
"""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from api.auth import require_admin, verify_api_key_header, verify_bootstrap_key
from api.gpu_metrics import router as gpu_metrics_router
from api.gpu_metrics import scrape_loop
from api.metrics import router as metrics_router
from api.models import (
    AdminGenerateAppKeyRequest,
    AdminGenerateUserKeyRequest,
    AdminSelfBootstrapRequest,
    ChatCompletionRequest,
    CreateAppRequest,
    CreateUserRequest,
    NullKeyRequest,
    SelfGenerateRequest,
    StandardResponse,
)
from celery_worker import celery_app as celery
from config import settings
from database.postgres import postgres_db
from database.timescale import timescale_db
from task_queue.processor import LLMProcessor
from task_queue.redis_client import redis_client
from utils.auth_cache import auth_cache
from utils.logger import logger
from utils.model_manager import model_manager

processors = {}


# ------------------------------------------------------------------
# Background task: clean up expired active slots every 10 seconds
# ------------------------------------------------------------------

async def slot_cleanup_loop():
    while True:
        try:
            await redis_client.cleanup_expired_slots()
        except Exception as e:
            logger.error(f"Slot cleanup error: {e}")
        await asyncio.sleep(10)


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------

async def _connect_with_retry(name: str, connect_fn, max_attempts: int = 10, base_delay: float = 3.0):
    for attempt in range(1, max_attempts + 1):
        try:
            await connect_fn()
            return
        except Exception as e:
            if attempt == max_attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), 60.0)
            logger.warning(f"{name}: attempt {attempt}/{max_attempts} failed — retrying in {delay:.0f}s | {e}")
            await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting EchoLog Gateway...")

    await _connect_with_retry("PostgreSQL", lambda: postgres_db.connect(
        settings.postgres_host, settings.postgres_port, settings.postgres_db,
        settings.postgres_user, settings.postgres_password,
        settings.postgres_pool_min_size, settings.postgres_pool_max_size,
    ))
    await _connect_with_retry("TimescaleDB", lambda: timescale_db.connect(
        settings.timescale_host, settings.timescale_port, settings.timescale_db,
        settings.timescale_user, settings.timescale_password,
        settings.timescale_pool_min_size, settings.timescale_pool_max_size,
    ))
    await _connect_with_retry("Redis", lambda: redis_client.connect(
        settings.redis_host, settings.redis_port, settings.redis_db, settings.redis_password,
    ))

    processors["general"] = LLMProcessor(
        "general", settings.general_llm_url, settings.general_llm_api_key,
        settings.general_llm_concurrent_limit,
    )
    processors["coding"] = LLMProcessor(
        "coding", settings.coding_llm_url, settings.coding_llm_api_key,
        settings.coding_llm_concurrent_limit,
    )

    asyncio.create_task(processors["general"].start())
    asyncio.create_task(processors["coding"].start())
    asyncio.create_task(slot_cleanup_loop())

    if settings.general_llm_metrics_url:
        asyncio.create_task(scrape_loop("general", settings.general_llm_metrics_url))
    if settings.coding_llm_metrics_url:
        asyncio.create_task(scrape_loop("coding", settings.coding_llm_metrics_url))

    if not settings.enable_auth:
        logger.warning("AUTHENTICATION DISABLED - Testing Mode")

    logger.info("Gateway ready!")
    yield

    logger.info("Shutting down...")
    await redis_client.close()
    await timescale_db.close()
    await postgres_db.close()


# ------------------------------------------------------------------
# FastAPI App
# ------------------------------------------------------------------

app = FastAPI(title="EchoLog Gateway", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(metrics_router)
app.include_router(gpu_metrics_router)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def wait_for_result(request_id: str, timeout: int = 300, poll_interval: float = 0.5):
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        result = await redis_client.get_response(request_id)
        if result:
            return result
        await asyncio.sleep(poll_interval)
    raise TimeoutError(f"Request {request_id} did not complete within {timeout} seconds")


async def stream_response(request_id: str, timeout: int = 300):
    chunk_index = 0
    start_time = time.time()
    poll_interval = 0.05

    try:
        while (time.time() - start_time) < timeout:
            chunk_key = f"stream:{request_id}:{chunk_index}"
            chunk_data = await redis_client.client.get(chunk_key)

            if chunk_data:
                chunk = json.loads(chunk_data)

                if chunk.get("error"):
                    yield {"event": "error", "data": json.dumps({"error": {"message": chunk["error"], "type": "stream_error"}})}
                    break

                if chunk.get("done"):
                    yield {"data": json.dumps({"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": chunk.get("model", "unknown"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}
                    yield {"data": "[DONE]"}
                    await redis_client.client.delete(chunk_key)
                    break

                yield {"data": json.dumps({"id": request_id, "object": "chat.completion.chunk", "created": chunk.get("created", int(time.time())), "model": chunk.get("model", "unknown"), "choices": [{"index": 0, "delta": {"content": chunk.get("content", "")}, "finish_reason": None}]})}
                await redis_client.client.delete(chunk_key)
                chunk_index += 1
            else:
                await asyncio.sleep(poll_interval)

        if (time.time() - start_time) >= timeout:
            yield {"event": "error", "data": json.dumps({"error": {"message": "Stream timeout", "type": "timeout"}})}
    except Exception as e:
        yield {"event": "error", "data": json.dumps({"error": {"message": str(e), "type": "internal_error"}})}


# ------------------------------------------------------------------
# Public Routes
# ------------------------------------------------------------------

@app.get("/")
async def root():
    return StandardResponse(success=True, data={
        "service": "EchoLog Gateway", "version": "3.0.0", "status": "running",
        "auth_enabled": settings.enable_auth,
    })


@app.get("/health")
async def health():
    checks = {}
    overall_status = "healthy"

    for label, db, purpose in [
        ("postgres", postgres_db, "user_management"),
        ("timescale", timescale_db, "request_logs"),
    ]:
        try:
            start = time.time()
            async with db.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks[label] = {"status": "healthy", "latency_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            overall_status = "degraded"
            checks[label] = {"status": "unhealthy", "error": str(e)}

    try:
        start = time.time()
        await redis_client.client.ping()
        checks["redis"] = {"status": "healthy", "latency_ms": int((time.time() - start) * 1000)}
    except Exception as e:
        overall_status = "degraded"
        checks["redis"] = {"status": "unhealthy", "error": str(e)}

    return {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "queues": {
            name: {"length": await redis_client.get_queue_length(name)}
            for name in ["general", "coding"]
        },
    }


# ------------------------------------------------------------------
# LLM Endpoints
# ------------------------------------------------------------------

@app.post("/{llm_type}/v1/chat/completions")
async def chat_completions(
    llm_type: str = Path(..., pattern="^(general|coding)$"),
    request: ChatCompletionRequest = ...,
    authorization: Optional[str] = Header(None),
):
    auth = await verify_api_key_header(authorization)

    request_id = str(uuid.uuid4())
    start_time = time.time()
    entity_type = auth.get("entity_type", "app")
    user_net_id = auth.get("user_net_id")
    app_name = auth.get("app_name")
    identifier = user_net_id if entity_type == "user" else app_name

    requested_model = request.model
    llm_url = settings.general_llm_url if llm_type == "general" else settings.coding_llm_url
    llm_key = settings.general_llm_api_key if llm_type == "general" else settings.coding_llm_api_key

    if not requested_model or requested_model == "auto":
        resolved_model = await model_manager.get_default_model(
            llm_type=llm_type, llm_url=llm_url, llm_api_key=llm_key, fallback="gpt-3.5-turbo",
        )
    else:
        resolved_model = requested_model

    semantic_cache_enabled = request.semantic_cache
    if semantic_cache_enabled is None:
        semantic_cache_enabled = auth.get("semantic_cache_enabled", False)

    # Batch-insert via Celery instead of direct DB write
    celery.send_task("celery_worker.buffer_log", args=[{
        "request_id": request_id,
        "entity_type": entity_type,
        "user_net_id": user_net_id,
        "app_name": app_name,
        "llm_type": llm_type,
        "requested_model": requested_model,
        "messages": [msg.model_dump() if hasattr(msg, "model_dump") else msg for msg in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "semantic_cache_enabled": semantic_cache_enabled,
    }])

    task_payload = {
        "request_id": request_id,
        "entity_type": entity_type,
        "user_net_id": user_net_id,
        "app_name": app_name,
        "model": resolved_model,
        "messages": [msg.model_dump() if hasattr(msg, "model_dump") else msg for msg in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": request.stream,
        "queue_start_time": time.time(),
        "total_start_time": start_time,
    }

    # Per-account rate limiting via Redis SADD/TTL
    accepted, status = await redis_client.check_and_enqueue(
        llm_type, task_payload, entity_type, identifier,
    )
    if not accepted:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {entity_type}:{identifier}. "
                   f"Try again when a current request completes.",
        )

    if request.stream:
        return EventSourceResponse(stream_response(request_id, timeout=300), media_type="text/event-stream")

    try:
        result = await wait_for_result(request_id, timeout=300)
        if result["status"] == "success":
            return result["response"]
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))


# ------------------------------------------------------------------
# Request Cancellation
# ------------------------------------------------------------------

@app.post("/api/v1/requests/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    authorization: Optional[str] = Header(None),
):
    await verify_api_key_header(authorization)
    await redis_client.set_cancel_flag(request_id)
    return StandardResponse(success=True, data={"request_id": request_id, "status": "cancellation_requested"})


# ------------------------------------------------------------------
# Models endpoint
# ------------------------------------------------------------------

@app.get("/{llm_type}/v1/models")
async def list_models(
    llm_type: str = Path(..., pattern="^(general|coding)$"),
    authorization: Optional[str] = Header(None),
):
    await verify_api_key_header(authorization)
    llm_url = settings.general_llm_url if llm_type == "general" else settings.coding_llm_url
    llm_key = settings.general_llm_api_key if llm_type == "general" else settings.coding_llm_api_key

    cached = model_manager.cache.get(llm_type)
    if cached:
        return {"object": "list", "data": [{"id": cached["model"], "object": "model"}], "from_cache": True}

    try:
        async with httpx.AsyncClient(timeout=settings.llm_health_check_timeout) as client:
            response = await client.get(f"{llm_url}/models", headers={"Authorization": f"Bearer {llm_key}"})
            response.raise_for_status()
            data = response.json()
            if "data" in data and data["data"]:
                model_manager.cache[llm_type] = {"model": data["data"][0]["id"], "updated_at": time.time()}
            return data
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Upstream {llm_type} LLM timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Auth / Admin endpoints
# ------------------------------------------------------------------

@app.post("/keys/self-generate")
async def self_generate_key(request: SelfGenerateRequest):
    plaintext = await postgres_db.generate_key_for_user(request.net_id)
    if plaintext is None:
        raise HTTPException(status_code=403, detail="Key generation failed.")
    return StandardResponse(success=True, data={"net_id": request.net_id, "api_key": plaintext})


@app.post("/admin/keys/self-bootstrap")
async def admin_self_bootstrap(request: AdminSelfBootstrapRequest, authorization: Optional[str] = Header(None)):
    verify_bootstrap_key(authorization)
    plaintext = await postgres_db.generate_key_for_admin(request.net_id)
    if plaintext is None:
        raise HTTPException(status_code=403, detail="Bootstrap failed.")
    return StandardResponse(success=True, data={"net_id": request.net_id, "api_key": plaintext})


@app.post("/admin/keys/user/generate")
async def admin_generate_user_key(request: AdminGenerateUserKeyRequest, auth: dict = Depends(require_admin)):
    plaintext = await postgres_db.admin_generate_key_for_user(request.net_id)
    if plaintext is None:
        raise HTTPException(status_code=403, detail=f"Key generation failed for '{request.net_id}'.")
    return StandardResponse(success=True, data={"net_id": request.net_id, "api_key": plaintext})


@app.post("/admin/keys/app/generate")
async def admin_generate_app_key(request: AdminGenerateAppKeyRequest, auth: dict = Depends(require_admin)):
    plaintext = await postgres_db.admin_generate_key_for_app(request.app_name)
    if plaintext is None:
        raise HTTPException(status_code=404, detail=f"App not found: '{request.app_name}'")
    return StandardResponse(success=True, data={"app_name": request.app_name, "api_key": plaintext})


@app.post("/admin/keys/null")
async def null_key(request: NullKeyRequest, auth: dict = Depends(require_admin)):
    if request.entity_type not in ("user", "app"):
        raise HTTPException(status_code=400, detail="entity_type must be 'user' or 'app'")
    success = await postgres_db.null_key(request.entity_type, request.identifier)
    if not success:
        raise HTTPException(status_code=404, detail=f"Not found: '{request.identifier}'")
    auth_cache.evict_by_identity(request.entity_type, request.identifier)
    return StandardResponse(success=True, data={"message": f"Key nulled for {request.entity_type}: '{request.identifier}'"})


@app.post("/admin/users/create")
async def create_user(request: CreateUserRequest, auth: dict = Depends(require_admin)):
    net_id = await postgres_db.create_user(
        first_name=request.first_name, last_name=request.last_name,
        net_id=request.net_id, is_admin=request.is_admin,
    )
    return StandardResponse(success=True, data={"net_id": net_id})


@app.post("/admin/apps/create")
async def create_app(request: CreateAppRequest, auth: dict = Depends(require_admin)):
    app_name = await postgres_db.create_app(
        app_name=request.app_name, description=request.description,
        created_by=auth["user_net_id"],
    )
    return StandardResponse(success=True, data={"app_name": app_name})


@app.get("/admin/users/list")
async def list_users(auth: dict = Depends(require_admin)):
    users = await postgres_db.list_users()
    return StandardResponse(success=True, data={"users": users, "total": len(users)})


@app.get("/admin/apps/list")
async def list_apps(auth: dict = Depends(require_admin)):
    apps = await postgres_db.list_apps()
    return StandardResponse(success=True, data={"apps": apps, "total": len(apps)})


@app.get("/debug/recent-logs")
async def debug_logs(auth: dict = Depends(require_admin)):
    logs = await timescale_db.get_recent_logs(limit=10)
    return {"logs": logs}


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.gateway_host, port=settings.gateway_port, log_level=settings.log_level.lower())

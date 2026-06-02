"""
task_queue/processor.py
Queue processor — consumes from llm_queue:{llm_type} and forwards to upstream LLM.

Active slot lifecycle:
  1. Gateway calls redis_client.check_and_enqueue() → SADD to active set
  2. Processor dequeues from llm_queue and calls upstream
  3. On completion (success or error), calls redis_client.release_active_slot()
     which SREM from the active set and promotes the next queued request
"""

import asyncio
import json
import time
from typing import Any, Dict, Optional

import httpx

from database.timescale import timescale_db
from task_queue.redis_client import redis_client
from utils.logger import logger


class LLMProcessor:
    def __init__(self, llm_type: str, llm_url: str, llm_api_key: str, concurrent_limit: int = 1):
        self.llm_type = llm_type
        self.llm_url = llm_url
        self.llm_api_key = llm_api_key
        self.concurrent_limit = concurrent_limit
        self._semaphore = asyncio.Semaphore(concurrent_limit)

    async def start(self):
        logger.info(f"Started queue processor: {self.llm_type.upper()} (limit: {self.concurrent_limit})")
        while True:
            try:
                await self._semaphore.acquire()
                request_data = await redis_client.dequeue(self.llm_type)
                if not request_data:
                    self._semaphore.release()
                    await asyncio.sleep(0.5)
                    continue
                asyncio.create_task(self._process_request(request_data))
            except Exception as e:
                logger.error(f"Error in {self.llm_type} processor: {e}")
                self._semaphore.release()
                await asyncio.sleep(1)

    async def _process_request(self, request_data: Dict[str, Any]):
        request_id = request_data["request_id"]
        queue_start_time = request_data["queue_start_time"]
        stream = request_data.get("stream", False)
        resolved_model = request_data.get("model", "unknown")

        try:
            queue_time_ms = int((time.time() - queue_start_time) * 1000)
            logger.info(f"Processing {request_id[:8]} | {self.llm_type} | Queue: {queue_time_ms}ms")

            if stream:
                await self._handle_streaming_request(request_data, queue_time_ms, resolved_model)
            else:
                await self._handle_non_streaming_request(request_data, queue_time_ms, resolved_model)

        except Exception as e:
            logger.error(f"[FAILED] {request_id[:8]}: {e}", exc_info=True)
            await timescale_db.update_request_error(
                request_id=request_id,
                error_message=str(e),
                status="error",
                queue_time_ms=int((time.time() - queue_start_time) * 1000),
            )
            await redis_client.store_response(request_id, {"status": "error", "error": str(e)})
        finally:
            self._semaphore.release()
            await redis_client.release_active_slot(request_id)

    async def _handle_streaming_request(
        self, request_data: Dict[str, Any], queue_time_ms: int, resolved_model: str
    ):
        request_id = request_data["request_id"]
        llm_request = self._build_llm_request(request_data, stream=True)
        llm_start_time = time.time()
        chunk_index = 0
        full_content = []
        input_tokens = 0
        output_tokens = 0

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.llm_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.llm_api_key}"},
                    json=llm_request,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if await redis_client.is_cancelled(request_id):
                            logger.info(f"[CANCELLED] {request_id[:8]} during stream")
                            await redis_client.client.setex(
                                f"stream:{request_id}:{chunk_index}", 360,
                                json.dumps({"done": True, "model": resolved_model}),
                            )
                            break

                        if not line.strip() or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data_str)
                            if chunk.get("choices"):
                                content = chunk["choices"][0].get("delta", {}).get("content", "")
                                if content:
                                    full_content.append(content)
                                    await redis_client.client.setex(
                                        f"stream:{request_id}:{chunk_index}", 360,
                                        json.dumps({"content": content, "model": resolved_model, "done": False}),
                                    )
                                    chunk_index += 1
                            if "usage" in chunk:
                                input_tokens = chunk["usage"].get("prompt_tokens", 0)
                                output_tokens = chunk["usage"].get("completion_tokens", 0)
                        except json.JSONDecodeError:
                            continue

            await redis_client.client.setex(
                f"stream:{request_id}:{chunk_index}", 360,
                json.dumps({"done": True, "model": resolved_model}),
            )

            llm_latency_ms = int((time.time() - llm_start_time) * 1000)
            total_latency_ms = int((time.time() - request_data["total_start_time"]) * 1000)

            await timescale_db.update_request_success(
                request_id=request_id,
                response_content="".join(full_content),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
                queue_time_ms=queue_time_ms,
                resolved_model=resolved_model,
                cache_hit=False,
            )

        except httpx.TimeoutException:
            await redis_client.client.setex(
                f"stream:{request_id}:{chunk_index}", 360,
                json.dumps({"error": "Request timeout", "done": True}),
            )
            await timescale_db.update_request_error(
                request_id=request_id, error_message="Stream timeout",
                status="timeout", queue_time_ms=queue_time_ms,
            )

    async def _handle_non_streaming_request(
        self, request_data: Dict[str, Any], queue_time_ms: int, resolved_model: str
    ):
        request_id = request_data["request_id"]
        llm_request = self._build_llm_request(request_data, stream=False)
        llm_start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"{self.llm_url}/chat/completions",
                    json=llm_request,
                    headers={"Authorization": f"Bearer {self.llm_api_key}"},
                )
                response.raise_for_status()
                llm_response = response.json()

        except httpx.TimeoutException:
            await timescale_db.update_request_error(
                request_id=request_id, error_message="Request timeout",
                status="timeout", queue_time_ms=queue_time_ms,
            )
            await redis_client.store_response(request_id, {"status": "error", "error": "Request timeout"})
            return

        llm_latency_ms = int((time.time() - llm_start_time) * 1000)
        total_latency_ms = int((time.time() - request_data["total_start_time"]) * 1000)

        response_content = self._extract_content(llm_response)
        input_tokens = llm_response.get("usage", {}).get("prompt_tokens", 0)
        output_tokens = llm_response.get("usage", {}).get("completion_tokens", 0)

        await timescale_db.update_request_success(
            request_id=request_id,
            response_content=response_content or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=total_latency_ms,
            llm_latency_ms=llm_latency_ms,
            queue_time_ms=queue_time_ms,
            resolved_model=resolved_model,
            cache_hit=False,
        )

        await redis_client.store_response(request_id, {
            "status": "success",
            "response": llm_response,
            "metadata": {
                "queue_time_ms": queue_time_ms,
                "llm_latency_ms": llm_latency_ms,
                "total_latency_ms": total_latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        })

    @staticmethod
    def _build_llm_request(request_data: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        payload = {
            "model": request_data["model"],
            "messages": request_data["messages"],
            "temperature": request_data.get("temperature", 0.7),
            "max_tokens": request_data.get("max_tokens", 500),
            "stream": stream,
        }
        return {k: v for k, v in payload.items() if v is not None}

    @staticmethod
    def _extract_content(llm_response: Dict[str, Any]) -> Optional[str]:
        try:
            choices = llm_response.get("choices", [])
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                if isinstance(message, dict):
                    return message.get("content")
        except Exception:
            pass
        return None

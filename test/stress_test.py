"""
stress_test.py
Comprehensive stress test for LLM Gateway

Tests:
- Concurrent requests (20 simultaneous)
- Queue separation (general vs coding)
- Semantic cache (enabled vs disabled)
- Streaming vs non-streaming
- Redis queue behavior
- Performance metrics
"""

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

import httpx

# Configuration
GATEWAY_URL = "http://localhost:8000"
API_KEY = "test-key"
TIMEOUT = 120  # 2 minutes per request


class StressTestRunner:
    def __init__(self):
        self.results = []
        self.errors = []
        self.queue_snapshots = []

    async def monitor_queues(self, duration: int = 30, interval: float = 0.5):
        """Monitor Redis queues during test execution"""
        print("\n📊 Starting queue monitor...")
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            while (time.time() - start_time) < duration:
                try:
                    response = await client.get(
                        f"{GATEWAY_URL}/health",
                        headers={"Authorization": f"Bearer {API_KEY}"},
                        timeout=5,
                    )

                    if response.status_code == 200:
                        health = response.json()
                        snapshot = {
                            "timestamp": time.time() - start_time,
                            "general_queue": health["queues"]["general"]["length"],
                            "general_active": health["queues"]["general"]["active"],
                            "coding_queue": health["queues"]["coding"]["length"],
                            "coding_active": health["queues"]["coding"]["active"],
                        }
                        self.queue_snapshots.append(snapshot)
                except Exception as e:
                    print(f"⚠️  Queue monitor error: {e}")

                await asyncio.sleep(interval)

        print("✅ Queue monitoring complete")

    async def make_request(
        self, request_id: int, llm_type: str, semantic_cache: bool, stream: bool = False
    ) -> Dict[str, Any]:
        """Make a single request and measure performance"""

        # Vary the questions to test semantic cache properly
        questions = [
            "What is the capital of France?",
            "Explain quantum computing in simple terms.",
            "Write a Python function to reverse a string.",
            "What are the benefits of exercise?",
            "How does photosynthesis work?",
            "What is machine learning?",
            "Explain the theory of relativity.",
            "What is the meaning of life?",
            "How do computers work?",
            "What is artificial intelligence?",
        ]

        question = questions[request_id % len(questions)]

        payload = {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Keep answers concise.",
                },
                {"role": "user", "content": question},
            ],
            "max_tokens": 200,
            "temperature": 0.7,
            "stream": stream,
            "semantic_cache": semantic_cache,
        }

        result = {
            "request_id": request_id,
            "llm_type": llm_type,
            "semantic_cache": semantic_cache,
            "stream": stream,
            "question": question,
            "success": False,
            "error": None,
            "status_code": None,
            "total_time": 0,
            "response_length": 0,
            "cache_hit": False,
            "queue_time_ms": None,
            "llm_latency_ms": None,
        }

        start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                if stream:
                    # Streaming request
                    full_response = []
                    async with client.stream(
                        "POST",
                        f"{GATEWAY_URL}/{llm_type}/v1/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {API_KEY}"},
                    ) as response:
                        result["status_code"] = response.status_code

                        if response.status_code == 200:
                            async for line in response.aiter_lines():
                                if line.startswith("data: "):
                                    data_str = line[6:]
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data_str)
                                        if chunk.get("choices"):
                                            content = (
                                                chunk["choices"][0]
                                                .get("delta", {})
                                                .get("content", "")
                                            )
                                            if content:
                                                full_response.append(content)
                                    except json.JSONDecodeError:
                                        continue

                            result["success"] = True
                            result["response_length"] = len("".join(full_response))
                else:
                    # Non-streaming request
                    response = await client.post(
                        f"{GATEWAY_URL}/{llm_type}/v1/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {API_KEY}"},
                    )

                    result["status_code"] = response.status_code

                    if response.status_code == 200:
                        data = response.json()
                        result["success"] = True

                        # Extract response content
                        if data.get("choices"):
                            content = (
                                data["choices"][0].get("message", {}).get("content", "")
                            )
                            result["response_length"] = len(content)

                        # Extract metadata if available (from your gateway)
                        # Note: This depends on your response format
                        if "metadata" in data:
                            result["cache_hit"] = data["metadata"].get(
                                "from_cache", False
                            )
                            result["queue_time_ms"] = data["metadata"].get(
                                "queue_time_ms"
                            )
                            result["llm_latency_ms"] = data["metadata"].get(
                                "llm_latency_ms"
                            )
                    else:
                        result["error"] = (
                            f"HTTP {response.status_code}: {response.text[:200]}"
                        )

        except asyncio.TimeoutError:
            result["error"] = "Request timeout"
        except Exception as e:
            result["error"] = str(e)

        result["total_time"] = time.time() - start_time

        return result

    async def run_concurrent_batch(self, batch_config: List[Dict[str, Any]]):
        """Run a batch of concurrent requests"""
        print(f"\n🚀 Launching {len(batch_config)} concurrent requests...")

        tasks = [
            self.make_request(
                request_id=config["id"],
                llm_type=config["llm_type"],
                semantic_cache=config["semantic_cache"],
                stream=config["stream"],
            )
            for config in batch_config
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.errors.append(
                    {"request_id": batch_config[i]["id"], "error": str(result)}
                )
                print(f"❌ Request {batch_config[i]['id']} failed: {result}")
            else:
                self.results.append(result)
                status = "✅" if result["success"] else "❌"
                cache = "💾" if result.get("cache_hit") else "🔄"
                print(
                    f"{status} {cache} Request {result['request_id']:2d} | "
                    f"{result['llm_type']:7s} | Cache: {result['semantic_cache']} | "
                    f"Time: {result['total_time']:.2f}s | "
                    f"Response: {result['response_length']} chars"
                )

    def print_summary(self):
        """Print comprehensive test summary"""
        print("\n" + "=" * 80)
        print("📊 STRESS TEST SUMMARY")
        print("=" * 80)

        if not self.results:
            print("❌ No successful results to analyze")
            return

        # Success rate
        successful = [r for r in self.results if r["success"]]
        print(
            f"\n✅ Success Rate: {len(successful)}/{len(self.results)} "
            f"({len(successful) / len(self.results) * 100:.1f}%)"
        )

        # By LLM type
        by_llm = defaultdict(list)
        for r in successful:
            by_llm[r["llm_type"]].append(r)

        print("\n📍 By LLM Type:")
        for llm_type, results in by_llm.items():
            avg_time = sum(r["total_time"] for r in results) / len(results)
            print(
                f"  {llm_type:8s}: {len(results):2d} requests | Avg time: {avg_time:.2f}s"
            )

        # Cache performance
        cached = [r for r in successful if r.get("cache_hit")]
        non_cached = [r for r in successful if not r.get("cache_hit")]

        print("\n💾 Cache Performance:")
        print(f"  Cache hits:   {len(cached):2d} requests")
        print(f"  Cache misses: {len(non_cached):2d} requests")

        if cached and non_cached:
            avg_cached_time = sum(r["total_time"] for r in cached) / len(cached)
            avg_non_cached_time = sum(r["total_time"] for r in non_cached) / len(
                non_cached
            )
            speedup = (
                avg_non_cached_time / avg_cached_time if avg_cached_time > 0 else 0
            )

            print(f"  Avg cache hit time:   {avg_cached_time:.2f}s")
            print(f"  Avg cache miss time:  {avg_non_cached_time:.2f}s")
            print(f"  Cache speedup:        {speedup:.2f}x faster")

        # Streaming vs non-streaming
        streaming = [r for r in successful if r["stream"]]
        non_streaming = [r for r in successful if not r["stream"]]

        print("\n🌊 Streaming vs Non-Streaming:")
        if streaming:
            avg_stream = sum(r["total_time"] for r in streaming) / len(streaming)
            print(
                f"  Streaming:     {len(streaming):2d} requests | Avg: {avg_stream:.2f}s"
            )
        if non_streaming:
            avg_non_stream = sum(r["total_time"] for r in non_streaming) / len(
                non_streaming
            )
            print(
                f"  Non-streaming: {len(non_streaming):2d} requests | Avg: {avg_non_stream:.2f}s"
            )

        # Response times
        times = [r["total_time"] for r in successful]
        print("\n⏱️  Response Times:")
        print(f"  Min:    {min(times):.2f}s")
        print(f"  Max:    {max(times):.2f}s")
        print(f"  Avg:    {sum(times) / len(times):.2f}s")
        print(f"  Median: {sorted(times)[len(times) // 2]:.2f}s")

        # Queue analysis
        if self.queue_snapshots:
            print(f"\n📊 Queue Analysis ({len(self.queue_snapshots)} snapshots):")

            max_general_queue = max(s["general_queue"] for s in self.queue_snapshots)
            max_coding_queue = max(s["coding_queue"] for s in self.queue_snapshots)

            print(f"  Max general queue depth: {max_general_queue}")
            print(f"  Max coding queue depth:  {max_coding_queue}")

            # Check if queues are properly separated
            general_had_items = any(
                s["general_queue"] > 0 for s in self.queue_snapshots
            )
            coding_had_items = any(s["coding_queue"] > 0 for s in self.queue_snapshots)

            if general_had_items and coding_had_items:
                print("  ✅ Both queues active (properly separated)")
            elif general_had_items:
                print("  ⚠️  Only general queue active")
            elif coding_had_items:
                print("  ⚠️  Only coding queue active")
            else:
                print("  ⚠️  No queue activity detected (too fast or issue)")

        # Errors
        if self.errors:
            print(f"\n❌ Errors ({len(self.errors)}):")
            for error in self.errors[:5]:  # Show first 5
                print(f"  Request {error['request_id']}: {error['error']}")
            if len(self.errors) > 5:
                print(f"  ... and {len(self.errors) - 5} more")

        print("\n" + "=" * 80)

    def save_detailed_results(self, filename: str = "stress_test_results.json"):
        """Save detailed results to JSON file"""
        data = {
            "timestamp": datetime.now().isoformat(),
            "total_requests": len(self.results) + len(self.errors),
            "successful": len([r for r in self.results if r["success"]]),
            "failed": len([r for r in self.results if not r["success"]])
            + len(self.errors),
            "results": self.results,
            "errors": self.errors,
            "queue_snapshots": self.queue_snapshots,
        }

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        print(f"\n💾 Detailed results saved to: {filename}")


async def main():
    """Main stress test execution"""
    runner = StressTestRunner()

    print("=" * 80)
    print("🔥 LLM GATEWAY STRESS TEST")
    print("=" * 80)
    print(f"Target: {GATEWAY_URL}")
    print("Total Requests: 20 concurrent")
    print("Configuration:")
    print("  • 10 requests with semantic_cache=True (default)")
    print("  • 10 requests with semantic_cache=False")
    print("  • Mix of general and coding LLMs")
    print("  • Mix of streaming and non-streaming")
    print("=" * 80)

    # Test configuration: 20 concurrent requests
    batch_config = [
        # GENERAL LLM with cache (5 requests)
        {"id": 1, "llm_type": "general", "semantic_cache": True, "stream": False},
        {"id": 2, "llm_type": "general", "semantic_cache": True, "stream": False},
        {"id": 3, "llm_type": "general", "semantic_cache": True, "stream": True},
        {"id": 4, "llm_type": "general", "semantic_cache": True, "stream": False},
        {"id": 5, "llm_type": "general", "semantic_cache": True, "stream": True},
        # GENERAL LLM without cache (5 requests)
        {"id": 6, "llm_type": "general", "semantic_cache": False, "stream": False},
        {"id": 7, "llm_type": "general", "semantic_cache": False, "stream": False},
        {"id": 8, "llm_type": "general", "semantic_cache": False, "stream": True},
        {"id": 9, "llm_type": "general", "semantic_cache": False, "stream": False},
        {"id": 10, "llm_type": "general", "semantic_cache": False, "stream": True},
        # CODING LLM with cache (5 requests)
        {"id": 11, "llm_type": "coding", "semantic_cache": True, "stream": False},
        {"id": 12, "llm_type": "coding", "semantic_cache": True, "stream": False},
        {"id": 13, "llm_type": "coding", "semantic_cache": True, "stream": True},
        {"id": 14, "llm_type": "coding", "semantic_cache": True, "stream": False},
        {"id": 15, "llm_type": "coding", "semantic_cache": True, "stream": True},
        # CODING LLM without cache (5 requests)
        {"id": 16, "llm_type": "coding", "semantic_cache": False, "stream": False},
        {"id": 17, "llm_type": "coding", "semantic_cache": False, "stream": False},
        {"id": 18, "llm_type": "coding", "semantic_cache": False, "stream": True},
        {"id": 19, "llm_type": "coding", "semantic_cache": False, "stream": False},
        {"id": 20, "llm_type": "coding", "semantic_cache": False, "stream": True},
    ]

    # Start queue monitor in background
    monitor_task = asyncio.create_task(runner.monitor_queues(duration=60))

    # Small delay to let monitor start
    await asyncio.sleep(1)

    # Run the stress test
    start_time = time.time()
    await runner.run_concurrent_batch(batch_config)
    total_duration = time.time() - start_time

    # Wait for monitor to finish
    await monitor_task

    # Print results
    print(f"\n⏱️  Total test duration: {total_duration:.2f}s")
    runner.print_summary()
    runner.save_detailed_results()

    print("\n✅ Stress test complete!")


if __name__ == "__main__":
    asyncio.run(main())

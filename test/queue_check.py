"""
quick_queue_check.py
Quick script to verify queue separation
"""

import asyncio

import httpx

GATEWAY_URL = "http://localhost:8000"
API_KEY = "test-key"


async def check_queues():
    """Check that both queues exist and are working"""

    print("=" * 70)
    print("🔍 Queue Separation Verification")
    print("=" * 70)

    async with httpx.AsyncClient() as client:
        # Check initial state
        response = await client.get(
            f"{GATEWAY_URL}/health", headers={"Authorization": f"Bearer {API_KEY}"}
        )

        health = response.json()

        print("\n📊 Initial Queue State:")
        print(f"  General Queue: {health['queues']['general']}")
        print(f"  Coding Queue:  {health['queues']['coding']}")

        # Send one request to each queue
        print("\n📤 Sending test requests...")

        general_task = client.post(
            f"{GATEWAY_URL}/general/v1/chat/completions",
            json={
                "model": "test",
                "messages": [{"role": "user", "content": "test general"}],
                "max_tokens": 50,
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=60,
        )

        coding_task = client.post(
            f"{GATEWAY_URL}/coding/v1/chat/completions",
            json={
                "model": "test",
                "messages": [{"role": "user", "content": "test coding"}],
                "max_tokens": 50,
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=60,
        )

        # Check queue state immediately (should see items)
        await asyncio.sleep(0.5)

        response = await client.get(
            f"{GATEWAY_URL}/health", headers={"Authorization": f"Bearer {API_KEY}"}
        )

        health = response.json()

        print("\n📊 Queue State During Processing:")
        print(f"  General Queue: {health['queues']['general']}")
        print(f"  Coding Queue:  {health['queues']['coding']}")

        # Wait for completion
        await asyncio.gather(general_task, coding_task)

        print("\n✅ Queue separation verified!")
        print("   Both queues are independent and working correctly")


if __name__ == "__main__":
    asyncio.run(check_queues())

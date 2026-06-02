"""
test/test_with_auth_openai.py
Complete authentication test using OpenAI Python client

Tests:
- 5 requests to general LLM
- 5 requests to coding LLM
- Mix of streaming and non-streaming
- Validates authentication works
- Shows queue separation
"""

import time
from datetime import datetime

from openai import OpenAI

# ============================================
# Configuration - UPDATE THIS WITH YOUR KEY
# ============================================

# Get your API key by running:
# curl -X POST http://localhost:8000/admin/keys/regenerate \
#   -H "Content-Type: application/json" \
#   -d '{"entity_type": "intern", "identifier": "axy2"}'

API_KEY = "534ea11b-7900-454d-9744-87f8d8a6d1c0"  # ←  THIS IS THE CORRECT KEY
GATEWAY_URL = "http://localhost:8000"

# ============================================
# Test Configuration
# ============================================

GENERAL_QUESTIONS = [
    "What is the capital of Chile?",
    "Explain quantum computing in simple terms.",
    "What are the health benefits of walking?",
    "How does photosynthesis work?",
    "What is the meaning of deep learning?",
]

CODING_QUESTIONS = [
    "Write a Python function to reverse a string.",
    "Create a function to check if a number is prime.",
    "Write a function to find the factorial of a number.",
    "Create a binary search implementation in Python.",
    "Write a function to merge two sorted lists.",
]

# ============================================
# Helper Functions
# ============================================


def print_header(text: str):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_separator():
    """Print separator line"""
    print("-" * 80)


def test_request(
    client: OpenAI, llm_type: str, question: str, request_num: int, stream: bool = False
):
    """
    Test a single request

    Args:
        client: OpenAI client instance
        llm_type: "general" or "coding"
        question: The question to ask
        request_num: Request number for display
        stream: Whether to stream the response
    """
    start_time = time.time()

    try:
        if stream:
            # Streaming request
            print(f"\n🌊 Request {request_num} ({llm_type.upper()}) [STREAMING]")
            print(f"   Question: {question}")
            print("   Response: ", end="", flush=True)

            stream_response = client.chat.completions.create(
                model="auto",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant. Keep answers concise.",
                    },
                    {"role": "user", "content": question},
                ],
                max_tokens=2500,
                temperature=0,
                stream=True,
            )

            full_response = []
            for chunk in stream_response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    print(content, end="", flush=True)
                    full_response.append(content)

            elapsed = time.time() - start_time
            print(f"\n   ✅ Success in {elapsed:.2f}s")

            return {
                "request_num": request_num,
                "llm_type": llm_type,
                "question": question,
                "response": "".join(full_response),
                "stream": True,
                "success": True,
                "elapsed": elapsed,
                "error": None,
            }

        else:
            # Non-streaming request
            print(f"\n📝 Request {request_num} ({llm_type.upper()}) [NON-STREAMING]")
            print(f"   Question: {question}")

            response = client.chat.completions.create(
                model="auto",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant. Keep answers concise.",
                    },
                    {"role": "user", "content": question},
                ],
                max_tokens=2500,
                temperature=0,
                stream=False,
            )

            elapsed = time.time() - start_time
            content = response.choices[0].message.content

            print(f"   Response: {content[:100]}{'...' if len(content) > 100 else ''}")
            print(f"   ✅ Success in {elapsed:.2f}s")
            print(
                f"   📊 Tokens: {response.usage.total_tokens} (in: {response.usage.prompt_tokens}, out: {response.usage.completion_tokens})"
            )

            return {
                "request_num": request_num,
                "llm_type": llm_type,
                "question": question,
                "response": content,
                "stream": False,
                "success": True,
                "elapsed": elapsed,
                "tokens": response.usage.total_tokens,
                "model": response.model,
                "error": None,
            }

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"   ❌ Failed in {elapsed:.2f}s: {str(e)[:100]}")

        return {
            "request_num": request_num,
            "llm_type": llm_type,
            "question": question,
            "response": None,
            "stream": stream,
            "success": False,
            "elapsed": elapsed,
            "error": str(e),
        }


def print_summary(results: list):
    """Print test summary"""
    print_header("TEST SUMMARY")

    total = len(results)
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    general_requests = [r for r in results if r["llm_type"] == "general"]
    coding_requests = [r for r in results if r["llm_type"] == "coding"]

    streaming = [r for r in results if r["stream"]]
    non_streaming = [r for r in results if not r["stream"]]

    print("\n📊 Overall Results:")
    print(f"   Total Requests:  {total}")
    print(
        f"   ✅ Successful:    {len(successful)} ({len(successful) / total * 100:.1f}%)"
    )
    print(f"   ❌ Failed:        {len(failed)} ({len(failed) / total * 100:.1f}%)")

    print("\n🎯 By LLM Type:")
    print(f"   General LLM:     {len(general_requests)} requests")
    print(
        f"      Success:      {len([r for r in general_requests if r['success']])}/{len(general_requests)}"
    )
    print(f"   Coding LLM:      {len(coding_requests)} requests")
    print(
        f"      Success:      {len([r for r in coding_requests if r['success']])}/{len(coding_requests)}"
    )

    print("\n🌊 By Mode:")
    print(f"   Streaming:       {len(streaming)} requests")
    print(f"   Non-streaming:   {len(non_streaming)} requests")

    if successful:
        avg_time = sum(r["elapsed"] for r in successful) / len(successful)
        min_time = min(r["elapsed"] for r in successful)
        max_time = max(r["elapsed"] for r in successful)

        print("\n⏱️  Response Times:")
        print(f"   Average:         {avg_time:.2f}s")
        print(f"   Min:             {min_time:.2f}s")
        print(f"   Max:             {max_time:.2f}s")

    if failed:
        print("\n❌ Failures:")
        for r in failed:
            print(f"   Request {r['request_num']} ({r['llm_type']}): {r['error'][:80]}")

    print("\n" + "=" * 80)


# ============================================
# Main Test Execution
# ============================================


def main():
    """Run all tests"""

    print_header("LLM GATEWAY AUTHENTICATION TEST")
    print(f"\n📍 Gateway URL: {GATEWAY_URL}")
    print(f"🔑 API Key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n📋 Test Plan:")
    print("   • 5 requests to General LLM")
    print("   • 5 requests to Coding LLM")
    print("   • Mix of streaming and non-streaming")

    # Initialize OpenAI clients
    general_client = OpenAI(api_key=API_KEY, base_url=f"{GATEWAY_URL}/general/v1")

    coding_client = OpenAI(api_key=API_KEY, base_url=f"{GATEWAY_URL}/coding/v1")

    results = []

    # ============================================
    # Test 1: General LLM Requests (5 requests)
    # ============================================

    print_header("PHASE 1: GENERAL LLM (5 Requests)")

    for i, question in enumerate(GENERAL_QUESTIONS, start=1):
        # Alternate between streaming and non-streaming
        stream = i % 2 == 0

        result = test_request(
            client=general_client,
            llm_type="general",
            question=question,
            request_num=i,
            stream=stream,
        )
        results.append(result)

        # Small delay between requests
        if i < len(GENERAL_QUESTIONS):
            time.sleep(0.5)

    print_separator()
    print(
        f"\n✅ Phase 1 Complete: {len([r for r in results if r['success']])}/5 successful"
    )

    # Small pause between phases
    time.sleep(1)

    # ============================================
    # Test 2: Coding LLM Requests (5 requests)
    # ============================================

    print_header("PHASE 2: CODING LLM (5 Requests)")

    for i, question in enumerate(CODING_QUESTIONS, start=6):
        # Alternate between streaming and non-streaming
        stream = i % 2 == 1

        result = test_request(
            client=coding_client,
            llm_type="coding",
            question=question,
            request_num=i,
            stream=stream,
        )
        results.append(result)

        # Small delay between requests
        if i < len(CODING_QUESTIONS) + 5:
            time.sleep(0.5)

    print_separator()
    print(
        f"\n✅ Phase 2 Complete: {len([r for r in results[-5:] if r['success']])}/5 successful"
    )

    # ============================================
    # Print Summary
    # ============================================

    print_summary(results)

    # ============================================
    # Test Invalid Key (Should Fail)
    # ============================================

    print_header("BONUS: INVALID KEY TEST (Should Fail)")

    invalid_client = OpenAI(
        api_key="invalid-key-12345", base_url=f"{GATEWAY_URL}/general/v1"
    )

    try:
        print("\n🔒 Testing with invalid API key...")
        response = invalid_client.chat.completions.create(
            model="auto",
            messages=[{"role": "user", "content": "This should fail"}],
            max_tokens=50,
        )
        print("   ❌ SECURITY ISSUE: Invalid key was accepted!")
    except Exception as e:
        print(f"   ✅ Correctly rejected: {str(e)[:100]}")

    print("\n" + "=" * 80)
    print("🎉 ALL TESTS COMPLETE!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()

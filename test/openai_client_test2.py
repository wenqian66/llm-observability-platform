"""
test_openai_client.py
Test gateway with official OpenAI Python client
"""

import time

from openai import OpenAI

# Initialize client
client = OpenAI(api_key="test-key", base_url="http://localhost:8000/general/v1")

print("=" * 70)
print("🧪 Testing OpenAI Client with LLM Gateway")
print("=" * 70)

# TEST 1: Non-streaming
print("\n" + "=" * 70)
print("📝 Test 1: Non-streaming Request")
print("=" * 70)

start = time.time()
response = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
        {"role": "system", "content": "You are a helpful AI assistant."},
        {"role": "user", "content": "What is 2+2? Answer in one sentence."},
    ],
    max_tokens=2500,
    stream=False,
    extra_body={"semantic_cache": True},  # Custom flag
)

print(f"\n✅ Response received in {time.time() - start:.2f}s")
print(f"Content: {response.choices[0].message.content}")
print(f"Usage: {response.usage}")
print(f"Model: {response.model}")

# TEST 2: Streaming
print("\n" + "=" * 70)
print("🌊 Test 2: Streaming Request")
print("=" * 70)

start = time.time()
stream = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
        {"role": "system", "content": "You are a helpful AI assistant."},
        {"role": "user", "content": "Count from 1 to 5 slowly with descriptions."},
    ],
    max_tokens=2500,
    stream=True,
    extra_body={"semantic_cache": False},
)

print("\nStreaming response:")
print("-" * 70)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)

print(f"\n\n✅ Stream completed in {time.time() - start:.2f}s")

# TEST 3: Reasoning model
print("\n" + "=" * 70)
print("🧠 Test 3: Reasoning Model (if available)")
print("=" * 70)

try:
    response = client.chat.completions.create(
        model="openai/o1-mini",  # Or whatever your reasoning model is
        messages=[
            {"role": "user", "content": "What's the best way to sort a list in Python?"}
        ],
        max_tokens=2500,
        stream=False,
    )

    print(f"\n✅ Response: {response.choices[0].message.content[:200]}...")
    if hasattr(response.choices[0].message, "reasoning_content"):
        print(
            f"Reasoning available: {bool(response.choices[0].message.reasoning_content)}"
        )
except Exception as e:
    print(f"⚠️  Reasoning model not available: {e}")

print("\n" + "=" * 70)
print("✅ All tests complete!")
print("=" * 70)

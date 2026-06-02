"""
test/auto_model_detection.py
Test automatic model detection
"""

from openai import OpenAI

client = OpenAI(api_key="test-key", base_url="http://localhost:8000/general/v1")

print("=" * 70)
print("🤖 Testing Automatic Model Detection")
print("=" * 70)

# Test 1: Use "auto" to let gateway detect model
print("\n📝 Test 1: model='auto' (gateway auto-detects)")
response = client.chat.completions.create(
    model="auto",  # Gateway will auto-detect actual model
    messages=[{"role": "user", "content": "What is 2+2?"}],
    max_tokens=100,
)
print(f"✅ Model detected and used: {response.model}")
print(f"Response: {response.choices[0].message.content}")

# Test 2: Streaming with auto-detection
print("\n📝 Test 2: Streaming with model='auto'")
stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Count from 1 to 3."}],
    max_tokens=100,
    stream=True,
)
print("Response: ", end="")
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()

# Test 3: Check available models
print("\n📝 Test 3: List available models from upstream")
models = client.models.list()
print("✅ Available models:")
for model in models.data[:5]:  # Show first 5
    print(f"   - {model.id}")

# Test 4: Coding LLM
print("\n📝 Test 4: Coding LLM with auto-detection")
coding_client = OpenAI(api_key="test-key", base_url="http://localhost:8000/coding/v1")
response = coding_client.chat.completions.create(
    model="auto",
    messages=[
        {"role": "user", "content": "Write a Python function to add two numbers."}
    ],
    max_tokens=200,
)
print(f"✅ Model used: {response.model}")
print(f"Response: {response.choices[0].message.content[:100]}...")

print("\n" + "=" * 70)
print("✅ Auto-detection working!")
print("💡 Users just need to specify model='auto'")
print("=" * 70)

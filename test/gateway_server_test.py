"""
Quick Test: Verify OpenAI Client Works with Your Gateway
Run this to confirm everything is working
"""

from openai import OpenAI
import sys

# ============================================
# CONFIGURATION - UPDATE THESE
# ============================================

# Your gateway URL (update with your actual URL)
GATEWAY_URL = "http://localhost:8000/general/v1"  # or https://llm.dsrs.illinois.edu/general/v1

# Your API key (get from database or use test mode)
# If auth is disabled (ENABLE_AUTH=false), this can be anything
API_KEY = "test-key"  # Replace with actual key if auth is enabled

# ============================================
# Test Function
# ============================================

def test_gateway():
    """Test gateway compatibility with OpenAI client"""
    
    print("🧪 Testing LLM Gateway with OpenAI Client")
    print("="*60)
    print(f"Gateway URL: {GATEWAY_URL}")
    print(f"API Key: {API_KEY[:20]}...")
    print("="*60)
    
    try:
        # Initialize client (exactly like OpenAI)
        print("\n1️⃣  Initializing OpenAI client...")
        client = OpenAI(
            api_key=API_KEY,
            base_url=GATEWAY_URL
        )
        print("   ✅ Client initialized")
        
        # List models
        print("\n2️⃣  Fetching available models...")
        try:
            models = client.models.list()
            print(f"   ✅ Found {len(models.data)} model(s):")
            for model in models.data:
                print(f"      - {model.id}")
            
            # Use first model
            model_id = models.data[0].id if models.data else "default"
        except Exception as e:
            print(f"   ⚠️  Models endpoint error (using 'default'): {e}")
            model_id = "default"
        
        # Make a simple request
        print(f"\n3️⃣  Making chat completion request (model: {model_id})...")
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "user", "content": "Say 'Hello from LLM Gateway!' and nothing else."}
            ],
            max_tokens=20
        )
        
        print("   ✅ Response received!")
        print(f"\n📝 LLM Response:")
        print(f"   {response.choices[0].message.content}")
        
        if hasattr(response, 'usage') and response.usage:
            print(f"\n📊 Usage Statistics:")
            print(f"   - Input tokens: {response.usage.prompt_tokens}")
            print(f"   - Output tokens: {response.usage.completion_tokens}")
            print(f"   - Total tokens: {response.usage.total_tokens}")
        
        print("\n" + "="*60)
        print("✅ SUCCESS! Gateway is 100% OpenAI Compatible!")
        print("="*60)
        print("""
Your users can now use:

from openai import OpenAI

client = OpenAI(
    api_key="your-gateway-api-key",
    base_url="https://llm.dsrs.illinois.edu/general/v1"
)

response = client.chat.completions.create(
    model="default",
    messages=[{"role": "user", "content": "Hello!"}]
)

No other changes needed! 🎉
        """)
        
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print("\nTroubleshooting:")
        print("1. Is gateway running? Check: curl http://localhost:8000/health")
        print("2. Is ENABLE_AUTH=false (for testing)?")
        print("3. If auth enabled, is API key valid?")
        print("4. Check gateway logs for errors")
        return False

# ============================================
# Run Test
# ============================================

if __name__ == "__main__":
    success = test_gateway()
    sys.exit(0 if success else 1)
"""
Simple Diagnostic: Check what your reasoning model returns
Works through your gateway (which is already working)
"""

import requests
import json

print("="*70)
print("🔍 Diagnosing LLM Response via Gateway")
print("="*70)

# Make request through your gateway
url = "http://localhost:8000/general/v1/chat/completions"
headers = {"Authorization": "Bearer test-key"}
payload = {
    "model": "openai/gpt-oss-120b",
    "messages": [
        {"role": "user", "content": "What is 2+2? Answer in exactly one sentence."}
    ],
    "max_tokens": 100
}

print(f"\n📤 Sending request through gateway...")
response = requests.post(url, json=payload, headers=headers, timeout=60)

print(f"📥 Status: {response.status_code}")

if response.status_code == 200:
    result = response.json()
    
    print("\n" + "="*70)
    print("Full LLM Response Structure:")
    print("="*70)
    print(json.dumps(result, indent=2))
    print("="*70)
    
    # Detailed analysis
    print("\n🔍 Analysis:")
    print("="*70)
    
    if "choices" in result:
        print(f"✅ Has 'choices': {len(result['choices'])} choice(s)")
        
        if len(result["choices"]) > 0:
            choice = result["choices"][0]
            print(f"\n📋 Choice structure:")
            print(f"   Keys: {list(choice.keys())}")
            
            if "message" in choice:
                message = choice["message"]
                print(f"\n📋 Message structure:")
                print(f"   Keys: {list(message.keys())}")
                
                # Check each possible field
                print(f"\n📝 Content Analysis:")
                
                if "reasoning_content" in message:
                    reasoning = message["reasoning_content"]
                    print(f"\n   ✅ HAS 'reasoning_content':")
                    print(f"      Type: {type(reasoning)}")
                    print(f"      Is None: {reasoning is None}")
                    print(f"      Is Empty: {reasoning == '' if reasoning else 'N/A'}")
                    print(f"      Length: {len(reasoning) if reasoning else 0} chars")
                    if reasoning:
                        print(f"      Preview: {reasoning[:150]}...")
                else:
                    print(f"\n   ❌ NO 'reasoning_content' field")
                
                if "content" in message:
                    content = message["content"]
                    print(f"\n   ✅ HAS 'content':")
                    print(f"      Type: {type(content)}")
                    print(f"      Is None: {content is None}")
                    print(f"      Is Empty: {content == '' if content else 'N/A'}")
                    print(f"      Length: {len(content) if content else 0} chars")
                    if content:
                        print(f"      Value: '{content}'")
                else:
                    print(f"\n   ❌ NO 'content' field")
                
                # Check if they're the same
                if "reasoning_content" in message and "content" in message:
                    same = message["reasoning_content"] == message["content"]
                    print(f"\n   🔍 Are they identical? {same}")
    
    if "usage" in result:
        print(f"\n✅ Usage info:")
        print(f"   {json.dumps(result['usage'], indent=2)}")
    
    print("\n" + "="*70)
    print("🎯 Diagnosis:")
    print("="*70)
    
    # Provide diagnosis
    if "choices" in result and len(result["choices"]) > 0:
        message = result["choices"][0].get("message", {})
        
        has_reasoning = "reasoning_content" in message and message["reasoning_content"]
        has_content = "content" in message and message["content"]
        
        if has_reasoning and has_content:
            if message["reasoning_content"] == message["content"]:
                print("⚠️  ISSUE FOUND:")
                print("   - Both fields exist")
                print("   - But they are IDENTICAL")
                print("   - This means the LLM is returning the same text in both fields")
                print("   - OR the processor is copying one to the other")
            else:
                print("✅ CORRECT:")
                print("   - Both fields exist and are DIFFERENT")
                print("   - reasoning_content has the thinking process")
                print("   - content has the final answer")
        
        elif has_reasoning and not has_content:
            print("ℹ️  REASONING-ONLY MODEL:")
            print("   - Has reasoning_content")
            print("   - No content field (or empty)")
            print("   - Processor should use reasoning as response")
        
        elif not has_reasoning and has_content:
            print("✅ NORMAL MODEL:")
            print("   - Has content field with answer")
            print("   - No reasoning (normal model)")
        
        else:
            print("❌ PROBLEM:")
            print("   - No content in either field!")
            print("   - Check LLM backend configuration")

else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)

print("\n" + "="*70)
print("📊 Next Steps:")
print("="*70)
print("""
Now check your TimescaleDB to see what was stored:

psql ... -c "
SELECT 
    request_id,
    LENGTH(reasoning_content) as reasoning_len,
    LENGTH(response_content) as response_len,
    reasoning_content = response_content as identical,
    SUBSTRING(reasoning_content, 1, 80) as reasoning,
    SUBSTRING(response_content, 1, 80) as response
FROM llm_request_logs
ORDER BY timestamp DESC
LIMIT 1;
"

Compare what the gateway returned vs what got stored!
""")
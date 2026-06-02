"""
Test script for LLM Gateway - General LLM Only
Tests queuing, logging, and multiple concurrent requests
Uses OpenAI-compatible client
"""

import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any
from openai import AsyncOpenAI


# ============================================
# Configuration
# ============================================

GATEWAY_BASE_URL = "http://localhost:8000/general/v1"
API_KEY = "test-api-key"  # Mock key when ENABLE_AUTH=false

# Test requests configuration
TEST_REQUESTS = [
    {
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "temperature": 0.7,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "What is the capital of France?"}],
        "temperature": 0.5,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "Explain quantum computing in one sentence"}],
        "temperature": 0.8,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "What is the meaning of life?"}],
        "temperature": 0.9,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "List 3 programming languages"}],
        "temperature": 0.5,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "What is the speed of light?"}],
        "temperature": 0.3,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "Who invented the telephone?"}],
        "temperature": 0.5,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "What is photosynthesis?"}],
        "temperature": 0.6,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "Name the planets in our solar system"}],
        "temperature": 0.4,
        "max_tokens": 1500
    },
    {
        "messages": [{"role": "user", "content": "What is gravity?"}],
        "temperature": 0.7,
        "max_tokens": 1500
    }
]


# ============================================
# Color codes for terminal output
# ============================================

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


# ============================================
# Helper Functions
# ============================================

def print_header(text: str):
    """Print formatted header"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*80}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(80)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*80}{Colors.ENDC}\n")


def print_success(text: str):
    """Print success message"""
    print(f"{Colors.OKGREEN}✅ {text}{Colors.ENDC}")


def print_info(text: str):
    """Print info message"""
    print(f"{Colors.OKCYAN}ℹ️  {text}{Colors.ENDC}")


def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.WARNING}⚠️  {text}{Colors.ENDC}")


def print_error(text: str):
    """Print error message"""
    print(f"{Colors.FAIL}❌ {text}{Colors.ENDC}")


# ============================================
# Test Functions
# ============================================

async def test_health_check():
    """Test 1: Health check endpoint"""
    print_header("TEST 1: Health Check")
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health", timeout=10.0)
            
            if response.status_code == 200:
                data = response.json()
                print_success(f"Gateway is {data['status']}")
                print_info(f"Auth enabled: {data['auth_enabled']}")
                
                # Check each service
                for service, check in data['checks'].items():
                    if check['status'] == 'healthy':
                        print_success(f"{service}: {check['status']} ({check.get('latency_ms', 'N/A')}ms)")
                    else:
                        print_error(f"{service}: {check['status']} - {check.get('error', 'Unknown error')}")
                
                # Check queues
                print_info(f"Queue status:")
                for llm_type, queue in data['queues'].items():
                    print(f"  • {llm_type}: {queue['length']} queued, {queue['active']}/{queue['limit']} active")
                
                return True
            else:
                print_error(f"Health check failed with status {response.status_code}")
                return False
    except Exception as e:
        print_error(f"Health check failed: {str(e)}")
        return False


async def submit_request(client: AsyncOpenAI, request_num: int, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a single request to the gateway"""
    try:
        start_time = time.time()
        
        # Submit request (returns immediately with request ID)
        response = await client.chat.completions.create(
            model="default",
            **request_data
        )
        
        elapsed = time.time() - start_time
        
        # Extract request ID from response
        request_id = response.id
        status = response.status if hasattr(response, 'status') else 'unknown'
        
        print_success(f"Request #{request_num} submitted: {request_id[:8]}... | "
                     f"Status: {status} | Time: {elapsed*1000:.0f}ms")
        
        return {
            "request_num": request_num,
            "request_id": request_id,
            "submit_time": elapsed,
            "status": status,
            "messages": request_data["messages"]
        }
    
    except Exception as e:
        print_error(f"Request #{request_num} exception: {str(e)}")
        return {"request_num": request_num, "status": "failed", "error": str(e)}


async def poll_result(client: AsyncOpenAI, request_id: str, request_num: int, max_attempts: int = 60) -> Dict[str, Any]:
    """Poll for request result using OpenAI client"""
    attempt = 0
    start_time = time.time()
    
    print_info(f"Polling for request #{request_num} ({request_id[:8]}...)")
    
    while attempt < max_attempts:
        try:
            import httpx
            # Poll using direct HTTP request since OpenAI SDK doesn't support async polling
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(
                    f"{GATEWAY_BASE_URL}/chat/completions/{request_id}",
                    headers={"Authorization": f"Bearer {API_KEY}"},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Check if it's a completion response (has 'choices') or still processing
                    if 'choices' in result:
                        # Completed
                        elapsed = time.time() - start_time
                        
                        # Extract response content
                        content = result['choices'][0]['message']['content'] if result['choices'] else ""
                        content_preview = content[:100] + "..." if len(content) > 100 else content
                        
                        print_success(f"Request #{request_num} completed: {request_id[:8]}... | "
                                    f"Total: {elapsed*1000:.0f}ms")
                        print_info(f"Response: {content_preview}")
                        
                        return {
                            "request_num": request_num,
                            "request_id": request_id,
                            "status": "completed",
                            "total_time": elapsed,
                            "response": result
                        }
                    
                    elif result.get('status') == 'processing':
                        if attempt % 5 == 0:  # Print every 5 attempts
                            print_info(f"Request #{request_num} still processing... (attempt {attempt + 1}/{max_attempts})")
                    
                elif response.status_code == 404:
                    print_error(f"Request #{request_num} not found: {request_id}")
                    return {
                        "request_num": request_num,
                        "request_id": request_id,
                        "status": "not_found"
                    }
                else:
                    print_warning(f"Request #{request_num} returned status {response.status_code}")
            
            attempt += 1
            await asyncio.sleep(2)  # Poll every 2 seconds
            
        except Exception as e:
            print_error(f"Error polling request #{request_num}: {str(e)}")
            attempt += 1
            await asyncio.sleep(2)
    
    # Timeout
    print_warning(f"Request #{request_num} timed out after {max_attempts * 2} seconds")
    return {
        "request_num": request_num,
        "request_id": request_id,
        "status": "timeout"
    }


async def test_submit_all_requests():
    """Test 2: Submit all requests"""
    print_header("TEST 2: Submit All Requests (General LLM)")
    
    submitted_requests = []
    
    # Create OpenAI client
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url=GATEWAY_BASE_URL
    )
    
    # Submit all requests concurrently
    tasks = [
        submit_request(client, i + 1, req)
        for i, req in enumerate(TEST_REQUESTS)
    ]
    
    results = await asyncio.gather(*tasks)
    
    # Filter successful submissions
    submitted_requests = [r for r in results if r.get('status') not in ['failed', 'unknown']]
    
    print_info(f"\nSuccessfully submitted: {len(submitted_requests)}/{len(TEST_REQUESTS)} requests")
    
    await client.close()
    
    return submitted_requests


async def test_poll_all_results(submitted_requests: List[Dict[str, Any]]):
    """Test 3: Poll for all results"""
    print_header("TEST 3: Poll for Results")
    
    # Create OpenAI client
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url=GATEWAY_BASE_URL
    )
    
    # Poll all requests concurrently
    tasks = [
        poll_result(client, req['request_id'], req['request_num'])
        for req in submitted_requests
    ]
    
    results = await asyncio.gather(*tasks)
    
    await client.close()
    
    return results


async def test_final_health_check():
    """Test 4: Final health check to see queue state"""
    print_header("TEST 4: Final Health Check")
    
    await asyncio.sleep(2)  # Wait a bit for queues to settle
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health", timeout=10.0)
            
            if response.status_code == 200:
                data = response.json()
                
                print_info(f"Final queue status:")
                for llm_type, queue in data['queues'].items():
                    print(f"  • {llm_type}: {queue['length']} queued, {queue['active']}/{queue['limit']} active")
                
                return True
            else:
                print_error(f"Health check failed with status {response.status_code}")
                return False
    except Exception as e:
        print_error(f"Health check failed: {str(e)}")
        return False


async def check_database_logs():
    """Test 5: Check TimescaleDB for logged requests"""
    print_header("TEST 5: Verify Database Logs")
    
    try:
        import asyncpg
        
        # Connect to TimescaleDB
        conn = await asyncpg.connect(
            host='localhost',
            port=5432,
            database='llm_metrics',
            user='admin',
            password=''  # Update if you have a password
        )
        
        # Get recent logs
        logs = await conn.fetch("""
            SELECT 
                request_id,
                intern_id,
                llm_type,
                status,
                latency_ms,
                queue_time_ms,
                llm_latency_ms,
                cache_hit,
                LENGTH(response_content) as response_length,
                timestamp
            FROM llm_request_logs
            WHERE timestamp > NOW() - INTERVAL '5 minutes'
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        
        if logs:
            print_success(f"Found {len(logs)} requests in TimescaleDB")
            print_info("\nRecent requests:")
            
            for i, log in enumerate(logs[:10], 1):  # Show first 10
                status_icon = "✅" if log['status'] == 'success' else "❌"
                cache_icon = " [CACHED]" if log['cache_hit'] else ""
                
                print(f"{status_icon} {i}. Request {str(log['request_id'])[:8]}... | "
                      f"Status: {log['status']}{cache_icon} | "
                      f"LLM: {log['llm_type']} | "
                      f"Latency: {log['latency_ms']}ms | "
                      f"Response: {log['response_length']} chars")
            
            # Statistics
            success_count = sum(1 for log in logs if log['status'] == 'success')
            avg_latency = sum(log['latency_ms'] or 0 for log in logs if log['latency_ms']) / len(logs) if logs else 0
            
            print_info(f"\n📊 Statistics:")
            print(f"  • Success rate: {success_count}/{len(logs)} ({success_count/len(logs)*100:.1f}%)")
            print(f"  • Average latency: {avg_latency:.0f}ms")
        else:
            print_warning("No requests found in TimescaleDB from the last 5 minutes")
        
        await conn.close()
        return True
        
    except Exception as e:
        print_error(f"Failed to check database: {str(e)}")
        print_info("This is expected if TimescaleDB is not accessible")
        return False


def print_summary(submitted: List[Dict[str, Any]], results: List[Dict[str, Any]]):
    """Print test summary"""
    print_header("TEST SUMMARY")
    
    total_submitted = len(submitted)
    completed = len([r for r in results if r.get('status') == 'completed'])
    failed = len([r for r in results if r.get('status') == 'failed'])
    timeout = len([r for r in results if r.get('status') == 'timeout'])
    
    print(f"📊 Statistics:")
    print(f"  • Total submitted: {total_submitted}")
    print(f"  • Completed: {completed}")
    print(f"  • Failed: {failed}")
    print(f"  • Timeout: {timeout}")
    
    # Calculate average times for completed requests
    completed_results = [r for r in results if r.get('status') == 'completed']
    if completed_results:
        avg_total = sum(r['total_time'] for r in completed_results) / len(completed_results)
        
        print(f"\n⏱️  Average Timings (completed requests):")
        print(f"  • Total time: {avg_total*1000:.0f}ms")
    
    # Success rate
    if total_submitted > 0:
        success_rate = (completed / total_submitted) * 100
        print(f"\n📈 Success Rate: {success_rate:.1f}%")
        
        if success_rate == 100:
            print_success("All requests completed successfully! 🎉")
        elif success_rate >= 80:
            print_warning("Most requests completed successfully")
        else:
            print_error("Many requests failed or timed out")


# ============================================
# Main Test Runner
# ============================================

async def main():
    """Run all tests"""
    print_header("LLM GATEWAY TEST SUITE - GENERAL LLM ONLY")
    print_info(f"Gateway URL: {GATEWAY_BASE_URL}")
    print_info(f"Total test requests: {len(TEST_REQUESTS)}")
    print_info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test 1: Initial health check
    if not await test_health_check():
        print_error("Gateway is not healthy. Aborting tests.")
        return
    
    # Small delay
    await asyncio.sleep(2)
    
    # Test 2: Submit all requests
    submitted_requests = await test_submit_all_requests()
    
    if not submitted_requests:
        print_error("No requests were successfully submitted. Aborting tests.")
        return
    
    # Small delay before polling
    await asyncio.sleep(3)
    
    # Test 3: Poll for all results
    results = await test_poll_all_results(submitted_requests)
    
    # Test 4: Final health check
    await test_final_health_check()
    
    # Test 5: Check database logs
    await check_database_logs()
    
    # Print summary
    print_summary(submitted_requests, results)
    
    print_info(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_warning("\n\nTest interrupted by user")
    except Exception as e:
        print_error(f"\n\nUnexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
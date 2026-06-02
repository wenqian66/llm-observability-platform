# LLM Gateway API Documentation

## Base URL
- Development: `http://localhost:8000`
- Production: `https://your-domain.com`

## Authentication

All endpoints (except `/health` and `/docs`) require authentication via Bearer token:
```
Authorization: Bearer llm-{intern_id}-{32-char-hex}
```

## Standard Response Format

All endpoints (except `/health` and `/docs`) return:

**Success:**
```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "timestamp": "2025-01-10T12:34:56Z"
}
```

**Error:**
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "message": "Error description"
  },
  "timestamp": "2025-01-10T12:34:56Z"
}
```

---

## Endpoints

### 1. Health Check

**GET** `/health`

Check system health (database, Redis, LLM backends).

**No authentication required**

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-01-10T12:34:56Z",
  "checks": {
    "database": { "status": "healthy", "latency_ms": 5 },
    "redis": { "status": "healthy", "latency_ms": 2 },
    "llm_general": { "status": "healthy", "url": "...", "latency_ms": 250 },
    "llm_coding": { "status": "healthy", "url": "...", "latency_ms": 300 }
  },
  "queues": {
    "general": { "length": 3, "active": 1, "limit": 1 },
    "coding": { "length": 0, "active": 0, "limit": 1 }
  }
}
```

---

### 2. Chat Completion

**POST** `/v1/chat/completions`

Queue a chat completion request.

**Authentication:** Required

**Request Body:**
```json
{
  "model": "default",
  "llm_type": "general",
  "messages": [
    { "role": "user", "content": "What is 2+2?" }
  ],
  "temperature": 0.7,
  "max_tokens": 500
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "request_id": "abc-123-...",
    "status": "queued",
    "llm_type": "general",
    "queue_position": 3,
    "message": "Request queued. Poll /v1/results/{request_id}"
  }
}
```

---

### 3. Get Result

**GET** `/v1/results/{request_id}`

Poll for request result.

**Authentication:** Required

**Response (Processing):**
```json
{
  "success": true,
  "data": {
    "status": "processing",
    "request_id": "abc-123",
    "queue_length": 2,
    "message": "Request is being processed..."
  }
}
```

**Response (Completed):**
```json
{
  "success": true,
  "data": {
    "status": "completed",
    "request_id": "abc-123",
    "response": {
      "id": "...",
      "choices": [
        {
          "message": {
            "role": "assistant",
            "content": "2+2 equals 4."
          }
        }
      ],
      "usage": {
        "prompt_tokens": 8,
        "completion_tokens": 6,
        "total_tokens": 14
      }
    },
    "metadata": {
      "queue_time_ms": 1200,
      "llm_latency_ms": 850,
      "total_latency_ms": 2050
    }
  }
}
```

---

## Admin Endpoints

**All admin endpoints require `is_admin = true` for the authenticated user.**

### 4. Generate API Keys

**POST** `/admin/generate-keys`

Generate API keys for all active interns without keys.

**Response:**
```json
{
  "success": true,
  "data": {
    "message": "Generated 3 new API keys",
    "keys": [
      {
        "intern_id": 2,
        "net_id": "jdoe2",
        "name": "John Doe",
        "api_key": "llm-2-abc123...",
        "created_at": "2025-01-10T12:34:56Z"
      }
    ]
  }
}
```

---

### 5. Generate Individual Key

**POST** `/admin/generate-key/{intern_id}`

Generate or regenerate API key for specific intern.

**Response:**
```json
{
  "success": true,
  "data": {
    "intern_id": 2,
    "net_id": "jdoe2",
    "name": "John Doe",
    "api_key": "llm-2-xyz789...",
    "created_at": "2025-01-10T12:34:56Z",
    "message": "API key generated. Save this - it won't be shown again."
  }
}
```

---

### 6. Revoke Key

**POST** `/admin/revoke-key/{intern_id}`

Revoke API key for specific intern.

---

### 7. Refresh All Keys

**POST** `/admin/refresh-keys`

Refresh ALL API keys (revokes old, generates new).

⚠️ **WARNING: Invalidates all existing keys**

---

### 8. List Interns

**GET** `/admin/interns`

List all interns with API key status.

**Response:**
```json
{
  "success": true,
  "data": {
    "total": 4,
    "interns": [
      {
        "id": 1,
        "net_id": "admin1",
        "first_name": "Admin",
        "last_name": "User",
        "is_active": true,
        "is_admin": true,
        "api_key_preview": "llm-1-abc123...",
        "key_active": true,
        "last_used_at": "2025-01-10T11:30:00Z"
      }
    ]
  }
}
```

---

### 9. Get Usage Stats

**GET** `/admin/usage/{intern_id}`

Get usage statistics for specific intern.

**Response:**
```json
{
  "success": true,
  "data": {
    "intern_id": 2,
    "net_id": "jdoe2",
    "name": "John Doe",
    "usage": {
      "total_requests": 150,
      "successful_requests": 145,
      "cached_requests": 30,
      "avg_latency_ms": 1250.5,
      "total_tokens_used": 45000,
      "last_request_at": "2025-01-10T12:00:00Z"
    }
  }
}
```

---

## Error Codes

- `HTTP_400` - Bad request
- `HTTP_401` - Unauthorized (invalid/missing API key)
- `HTTP_403` - Forbidden (admin access required)
- `HTTP_404` - Resource not found
- `HTTP_500` - Internal server error
- `REQUEST_FAILED` - LLM request failed
- `INTERNAL_SERVER_ERROR` - Unexpected error

---

## Rate Limiting

Currently not enforced. Future implementation will use `rate_limit_per_hour` column.

---

## Integration with Semantic Cache Team

**Redis Pub/Sub Channel:** `semantic_cache_results`

**Queue Keys:**
- `llm_queue:general`
- `llm_queue:coding`

**Message Format (to cache team):**
```json
{
  "request_id": "abc-123",
  "intern_id": 2,
  "messages": [...],
  "temperature": 0.7,
  "max_tokens": 500
}
```

**Response Format (from cache team):**
```json
{
  "request_id": "abc-123",
  "cache_hit": true,
  "response": "...",
  "source": "semantic_cache"
}
```
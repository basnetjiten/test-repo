# LightRAG API Integration Guide

For external projects consuming this LightRAG instance.

## Base URL

| Scenario | URL |
|---|---|
| Local (same host) | `http://localhost:9621` |
| Docker network | `http://lightrag-api:9621` |
| With Nginx proxy | `http://localhost:8080` |

## Authentication

All endpoints except `/health` and `/auth-status` require one of:

- **X-API-Key header** (recommended for service-to-service):
  ```
  X-API-Key: rH8Q1J6mWKNS1sREkzp3b2Yh79FpHjhA
  ```
- **JWT Bearer token** (obtained via `/login`):
  ```
  Authorization: Bearer <token>
  ```

## Endpoints

### 1. Document Ingestion

**Upload a file:**
```bash
curl -X POST http://localhost:9621/documents/upload \
  -H "X-API-Key: your-key" \
  -F "file=@/path/to/document.pdf"
```

**Ingest text directly:**
```bash
curl -X POST http://localhost:9621/documents/text \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"text": "Your document content here...", "description": "My document"}'
```

**Ingest multiple texts:**
```bash
curl -X POST http://localhost:9621/documents/texts \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '["Text one", "Text two", "Text three"]'
```

**Track processing status:**
```bash
curl -s http://localhost:9621/documents/track_status/{track_id} \
  -H "X-API-Key: your-key"
```

**List all documents (paginated):**
```bash
curl -X POST http://localhost:9621/documents/paginated \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"page": 1, "page_size": 20, "status": "processing"}'
```

**Get pipeline status:**
```bash
curl -s http://localhost:9621/documents/pipeline_status \
  -H "X-API-Key: your-key"
```

### 2. Querying

**Standard query (mode = hybrid):**
```bash
curl -X POST http://localhost:9621/query \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is this document about?",
    "mode": "hybrid",
    "include_references": true
  }'
```

Available modes: `local`, `global`, `hybrid`, `naive`, `mix`.

**Streaming query (SSE):**
```bash
curl -N -X POST http://localhost:9621/query/stream \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Summarize the key findings",
    "mode": "mix"
  }'
```

### 3. Graph Operations

**List graph labels:**
```bash
curl -s http://localhost:9621/graph/label/list \
  -H "X-API-Key: your-key"
```

**Get graphs:**
```bash
curl -s http://localhost:9621/graphs \
  -H "X-API-Key: your-key"
```

**Create an entity:**
```bash
curl -X POST http://localhost:9621/graph/entity/create \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "entity_name": "LightRAG",
    "entity_type": "Framework",
    "description": "A graph-based RAG system"
  }'
```

### 4. Ollama-Compatible API

**Chat (with RAG):**
```bash
curl -X POST http://localhost:9621/api/chat \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "lightrag:latest",
    "messages": [
      {"role": "user", "content": "/hybrid What is LightRAG?"}
    ],
    "stream": false
  }'
```

Query mode prefixes: `/local`, `/global`, `/hybrid`, `/naive`, `/mix`, `/bypass`, `/context`.

**Generate (bypass to LLM):**
```bash
curl -X POST http://localhost:9621/api/generate \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "lightrag:latest",
    "prompt": "Hello, how are you?",
    "stream": false
  }'
```

## Python Client Example

```python
import httpx

BASE = "http://localhost:9621"
KEY = "your-lightrag-api-key"
HEADERS = {"X-API-Key": KEY}

# Upload a document
with open("report.pdf", "rb") as f:
    r = httpx.post(f"{BASE}/documents/upload", headers=HEADERS, files={"file": f})
    track_id = r.json()["track_ids"][0]

# Wait for indexing
while True:
    r = httpx.get(f"{BASE}/documents/track_status/{track_id}", headers=HEADERS)
    status = r.json()["data"]["status"]
    if status in ("completed", "failed"):
        break

# Query
r = httpx.post(
    f"{BASE}/query",
    headers=HEADERS,
    json={"query": "What does this document cover?", "mode": "hybrid"},
)
print(r.json()["response"])
```

## Health Check

```bash
curl -s http://localhost:9621/health | python3 -m json.tool
```

Unauthenticated callers receive liveness signals only. Authenticated callers receive the full runtime configuration.

## OpenAPI Schema

```bash
# Fetch the full machine-readable API spec
curl -s http://localhost:9621/openapi.json | python3 -m json.tool
```

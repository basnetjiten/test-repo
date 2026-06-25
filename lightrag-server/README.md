# lightrag-server

Standalone, production-ready LightRAG deployment for EBProcess.
Powered by **OpenAI** (LLM + embeddings) with **PostgreSQL** (vector/KV storage) and **NetworkX** (graph).
Pinned to **v1.5.0rc2**.

---

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env — set your OpenAI API key:
#   LLM_BINDING_API_KEY=sk-...
#   EMBEDDING_BINDING_API_KEY=sk-...
```

### 2. Start

```bash
make init   # create ./data dirs
make up     # start Postgres + LightRAG
```

### 3. Verify

```bash
make health
# → {"status":"ok","working_dir":"/app/data/rag_storage",...}

# Browse:
# WebUI:   http://localhost:9621/webui
# Swagger: http://localhost:9621/docs
```

---

## API Surface

All endpoints are documented at `/openapi.json` and browsable at `/docs`.

| Category | Method | Path | Description |
|---|---|---|---|
| **Document** | POST | `/documents/upload` | Upload a file (PDF, DOCX, TXT…) |
| **Document** | POST | `/documents/text` | Ingest raw text inline |
| **Document** | POST | `/documents/texts` | Batch ingest multiple texts |
| **Document** | POST | `/documents/scan` | Scan the server's input directory |
| **Document** | GET | `/documents/track_status/{id}` | Poll processing status |
| **Document** | GET | `/documents/pipeline_status` | Overall pipeline status |
| **Document** | POST | `/documents/paginated` | List documents (paginated) |
| **Query** | POST | `/query` | Hybrid / local / global / mix / naive |
| **Query** | POST | `/query/stream` | Streaming query (SSE) |
| **Query** | POST | `/query/data` | Query with structured data response |
| **Graph** | GET | `/graphs` | List available graphs |
| **Graph** | GET | `/graph/label/list` | List graph labels |
| **Graph** | POST | `/graph/entity/create` | Create entity |
| **Graph** | POST | `/graph/relation/create` | Create relation |
| **Ollama** | POST | `/api/chat` | Ollama-compatible chat |
| **Ollama** | POST | `/api/generate` | Ollama-compatible generate |
| **System** | GET | `/health` | Liveness / readiness |
| **System** | GET | `/auth-status` | Authentication status |

### Authentication

Two layers (both configured in `.env`):

1. **API key** — pass in `X-API-Key` header (for service-to-service):
   ```bash
   curl -H "X-API-Key: $LIGHTRAG_API_KEY" \
        -H "Content-Type: application/json" \
        http://localhost:9621/query \
        -d '{"query":"What is LightRAG?","mode":"hybrid"}'
   ```

2. **Web login** — JWT-based via the `/webui` login screen.
   Generate a bcrypt password hash:
   ```bash
   docker compose exec lightrag lightrag-hash-password --username admin
   ```

> Both must be configured before exposing the server beyond `localhost`.

### Connecting from another Docker project

```yaml
# other-project/docker-compose.yml
services:
  my-service:
    networks:
      - lightrag-shared

networks:
  lightrag-shared:
    external: true
```

Access LightRAG at `http://lightrag-api:9621` from that project.

---

## Examples

See the [`examples/`](./examples/) directory for ready-to-run Python scripts:

| File | What it shows |
|---|---|
| `01_ingest_text.py` | Ingest text via REST API |
| `02_query_modes.py` | All 5 query modes (naive/local/global/hybrid/mix) |
| `03_upload_file.py` | Upload a file + poll until indexed |
| `04_openai_sdk_demo.py` | Direct LightRAG Python SDK with OpenAI |

```bash
cd examples
cp .env.example .env          # set LIGHTRAG_API_KEY + OPENAI_API_KEY
pip install requests httpx python-dotenv
python 01_ingest_text.py
python 02_query_modes.py
```

---

## Operations

| Command | Description |
|---|---|
| `make init` | Create data dirs and copy .env template |
| `make up` | Start Postgres + LightRAG |
| `make up-proxy` | Start with Nginx reverse proxy (port 8080) |
| `make up-ollama` | Start with local Ollama LLM profile |
| `make down` | Stop all services |
| `make logs` | Follow LightRAG logs |
| `make health` | Hit `/health` and pretty-print |
| `make smoke` | Run full API endpoint smoke test |
| `make clean-data` | ⚠️ Wipe all indexed data (re-index required) |
| `make pull-image` | Pull the pinned image version |

---

## Data Persistence

| Data | Location |
|---|---|
| LightRAG working state + graph | `./data/rag_storage/` |
| Upload staging | `./data/inputs/` |
| Prompt templates | `./data/prompts/` |
| PostgreSQL (KV + vector) | `postgres_data` Docker volume |
| Ollama models (if used) | `ollama_data` Docker volume |

> **Important:** Do not change `EMBEDDING_MODEL`, `EMBEDDING_DIM`, or `LIGHTRAG_GRAPH_STORAGE`
> after the first document has been indexed. If you need to change these, run `make clean-data` first.

---

## TLS / Reverse Proxy

The included Nginx profile adds upload/streaming tuning on port 8080:

```bash
docker compose --profile proxy up -d
```

See [`nginx/lightrag.conf`](./nginx/lightrag.conf) for the full configuration.

---

## Architecture

```
Client → [X-API-Key]
          ↓
       Nginx (optional, port 8080)
          ↓
    LightRAG gunicorn (port 9621)
          ↓              ↓
   PostgreSQL       NetworkX graph
   (KV + vector)   (./data/rag_storage/)
          ↓
   OpenAI API (LLM + embedding)
```

For the long-term integration strategy with `ebprocess-ingestion` and `ebprocess-edge-api`,
see [`integration.md`](./integration.md).

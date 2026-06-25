# LightRAG Server — Examples

Ready-to-run Python examples showing how to use the LightRAG REST API and Python SDK.

## Prerequisites

1. LightRAG server must be running (`make up` from the project root)
2. Copy and fill in `.env.example`:
   ```bash
   cp .env.example .env
   # Set LIGHTRAG_BASE_URL, LIGHTRAG_API_KEY, OPENAI_API_KEY
   ```
3. Install dependencies:
   ```bash
   pip install requests httpx python-dotenv lightrag-hku[api]
   ```

## Examples

### 01 — Ingest Text (`01_ingest_text.py`)

Posts a raw text string to `/documents/text` and polls until it is fully indexed.

```bash
python 01_ingest_text.py
```

**What it demonstrates:**
- Authenticating with `X-API-Key`
- Posting text to `/documents/text`
- Polling `/documents/track_status/{id}` until indexing completes

---

### 02 — Query Modes (`02_query_modes.py`)

Runs all five LightRAG query modes against the indexed knowledge base.

```bash
python 02_query_modes.py
```

**Query modes explained:**
| Mode | Best for |
|---|---|
| `naive` | Simple keyword search, fast |
| `local` | Entity-centric, precise answers |
| `global` | Big-picture, thematic queries |
| `hybrid` | Balanced (recommended default) |
| `mix` | Combines local + global graphs |

---

### 03 — Upload File (`03_upload_file.py`)

Uploads a file from disk and waits for it to be indexed.

```bash
python 03_upload_file.py
# or specify a custom file:
python 03_upload_file.py ./sample_data/book.txt
```

**What it demonstrates:**
- Multipart file upload to `/documents/upload`
- Polling pipeline status until the document is processed

---

### 04 — OpenAI SDK Demo (`04_openai_sdk_demo.py`)

Uses the **LightRAG Python SDK** directly (not the REST API). Shows how to initialise
a LightRAG instance with OpenAI, insert documents, and run queries from Python code.
Based on the [official `lightrag_openai_demo.py`](https://github.com/HKUDS/LightRAG/blob/main/examples/lightrag_openai_demo.py).

```bash
python 04_openai_sdk_demo.py
```

**What it demonstrates:**
- Initialising `LightRAG` with OpenAI LLM + embeddings
- Inserting text directly via the SDK
- Running all query modes programmatically

---

## Sample Data

`sample_data/book.txt` — A short public-domain excerpt (A Christmas Carol) used for
testing. Small enough to index quickly without burning many API tokens.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LIGHTRAG_BASE_URL` | No | `http://localhost:9621` | Server URL |
| `LIGHTRAG_API_KEY` | Yes | — | `X-API-Key` header value (from server `.env`) |
| `OPENAI_API_KEY` | Only for `04_*` | — | OpenAI key for SDK demo |

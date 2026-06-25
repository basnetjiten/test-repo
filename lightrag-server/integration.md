# Future Integration Plan

This document defines the planned integration approach between:

- `lightrag-server`
- `ebprocess-ingestion`
- `ebprocess-edge-api`

Current phase: `lightrag-server` runs standalone. No storage integration with
`ebprocess-ingestion` is implemented yet, and no LightRAG API exposure through
`ebprocess-edge-api` is implemented yet.

## 1) Target Responsibilities

- `lightrag-server`
  - Owns LightRAG runtime, document indexing pipeline, retrieval, and query APIs.
  - Owns LightRAG-internal storage schema/indices/workspaces.
  - Exposes operational endpoints (`/health`, `/auth-status`) and API docs.
- `ebprocess-ingestion`
  - Owns source document lifecycle, metadata, scanning, and object storage.
  - Remains system-of-record for source files and ingestion status.
- `ebprocess-edge-api`
  - Becomes the single external API surface for clients.
  - Handles client authn/authz, request shaping, versioning, and policy controls.
  - Proxies/orchestrates calls to `lightrag-server` and other backend services.

## 2) Planned Integration Boundaries

- Between `ebprocess-ingestion` and `lightrag-server`
  - Contract type: internal service-to-service integration.
  - Data exchanged:
    - Source document references (object keys, metadata, tenant/workspace context).
    - Indexing triggers (new/updated/deleted source events).
    - Processing status callbacks or polling checkpoints.
  - Principle: `ebprocess-ingestion` remains source authority; `lightrag-server`
    maintains derived retrieval/index data.

- Between `ebprocess-edge-api` and `lightrag-server`
  - Contract type: API gateway/proxy integration.
  - Data exchanged:
    - Upload or source-selection requests (depending on final workflow choice).
    - Query requests/responses, streaming responses, references, and status checks.
  - Principle: `ebprocess-edge-api` controls public API policy and security;
    `lightrag-server` stays internal.

## 3) Proposed Phased Rollout

### Phase A: Standalone stabilization (current)

- Deploy `lightrag-server` independently with persistent storage.
- Validate API completeness and runtime stability under load.
- Baseline observability, backup strategy, and operational runbooks.

### Phase B: Storage and ingestion alignment

- Define canonical document identity and workspace mapping:
  - `source_id` (ingestion)
  - `workspace`/tenant namespace (LightRAG)
  - idempotency key for re-indexing
- Introduce ingestion-to-LightRAG indexing trigger flow:
  - event-driven (preferred) or polling fallback.
- Decide ingestion mode:
  - direct file upload to LightRAG, or
  - text/materialized content handoff from ingestion pipeline.

### Phase C: Edge API exposure

- Add `ebprocess-edge-api` routes that proxy/compose LightRAG APIs.
- Enforce org/project/member authorization before forwarding requests.
- Normalize response contracts and error envelopes for external consumers.

### Phase D: Production hardening

- Rate limiting, quota policy, and workload isolation by workspace.
- SLOs for ingestion latency, query latency, and indexing success rates.
- Disaster recovery drills for Postgres/Neo4j and re-index workflows.

## 4) Key Design Decisions (for future implementation)

- Source of truth
  - Source files and lifecycle remain owned by `ebprocess-ingestion`.
  - LightRAG holds derived/indexed data and retrieval graph/vector state.
- Identity mapping
  - Keep a stable cross-service mapping: `source_id -> lightrag_doc_id`.
  - Store mapping in an integration table/service, not in client-visible payloads.
- Deletion semantics
  - Source delete/tombstone must trigger deterministic LightRAG cleanup.
  - Re-index must be idempotent and safe for retries.
- Multi-tenancy
  - Use LightRAG `WORKSPACE` (or storage-specific workspace vars) per tenant/project.
  - Ensure strict workspace isolation in all proxy and trigger flows.

## 5) Security Model (target)

- Network
  - Keep `lightrag-server` private/internal; expose only via `ebprocess-edge-api`.
- Service auth
  - Use service-to-service credentials between edge/ingestion and LightRAG.
  - Rotate secrets and avoid embedding static credentials in images.
- End-user auth
  - Performed at `ebprocess-edge-api`.
  - Edge propagates only scoped internal identity/context downstream.

## 6) Observability and Operability (target)

- Required telemetry
  - Correlation IDs across all three services.
  - Indexing job lifecycle metrics (queued, processing, failed, completed).
  - Query latency, rerank latency, and upstream LLM provider errors.
- Operational controls
  - Retry/backoff for indexing trigger failures.
  - Dead-letter handling for unrecoverable indexing events.
  - Reconciliation job to detect and repair ingestion/index drift.

## 7) Open Items Before Integration Implementation

- Final event contract and schema for ingestion-to-LightRAG triggers.
- Canonical tenant/workspace naming convention across services.
- Query response contract to expose externally from `ebprocess-edge-api`.
- Backfill strategy for existing ingestion documents.

## 8) Non-Goals in This Phase

- No direct storage coupling into `ebprocess-ingestion` yet.
- No LightRAG API routes added to `ebprocess-edge-api` yet.
- No public exposure of `lightrag-server` as a platform edge endpoint yet.

## 9) Standard Container Communication (Docker Network)

To consume the LightRAG API from other services (such as `ebprocess-ingestion` or `ebprocess-edge-api`) when running in separate Docker containers, use the shared Docker network:

### 1. Join the Shared Network
In each consuming service's `docker-compose.yml`, declare the `lightrag-shared` network as external and join it:

```yaml
services:
  ebprocess-edge-api:
    # ... your service config ...
    environment:
      # Use LightRAG container name as the host
      LIGHTRAG_URL: "http://lightrag:9621"
      LIGHTRAG_API_KEY: "rH8Q1J6mWKNS1sREkzp3b2Yh79FpHjhA"
    networks:
      - lightrag-shared
      - default

networks:
  lightrag-shared:
    external: true
```

### 2. Service API Authentication
All programmatic calls from other containers must pass the API key in the `X-API-Key` header:

```http
POST http://lightrag:9621/query
X-API-Key: rH8Q1J6mWKNS1sREkzp3b2Yh79FpHjhA
Content-Type: application/json

{
  "query": "What is the status of the project?",
  "mode": "hybrid"
}
```


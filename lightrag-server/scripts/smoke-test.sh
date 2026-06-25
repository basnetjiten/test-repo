#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LIGHTRAG_BASE_URL:-http://localhost:9621}"
TMP_OPENAPI="$(mktemp)"
trap 'rm -f "$TMP_OPENAPI"' EXIT

echo "[smoke] Fetching OpenAPI spec from ${BASE_URL}/openapi.json"
curl -fsS "${BASE_URL}/openapi.json" -o "$TMP_OPENAPI"

python - "$TMP_OPENAPI" <<'PY'
import json
import sys

spec_path = sys.argv[1]
with open(spec_path, "r", encoding="utf-8") as f:
    spec = json.load(f)

paths = set(spec.get("paths", {}).keys())
required = {
    "/health": {"get"},
    "/auth-status": {"get"},
    "/documents/upload": {"post"},
    "/documents/scan": {"post"},
    "/documents/text": {"post"},
    "/documents/texts": {"post"},
    "/documents/track_status/{track_id}": {"get"},
    "/documents/pipeline_status": {"get"},
    "/documents/paginated": {"post"},
    "/query": {"post"},
    "/query/stream": {"post"},
    "/query/data": {"post"},
    "/graphs": {"get"},
    "/graph/label/list": {"get"},
    "/api/tags": {"get"},
    "/api/version": {"get"},
    "/api/chat": {"post"},
    "/api/generate": {"post"},
}

missing = sorted([p for p in required if p not in paths])
if missing:
    print("[smoke] Missing required API endpoints:")
    for path in missing:
        print(f"  - {path}")
    raise SystemExit(1)

missing_methods = []
for path, methods in required.items():
    available = set((spec.get("paths", {}).get(path) or {}).keys())
    for method in methods:
        if method not in available:
            missing_methods.append((method.upper(), path))

if missing_methods:
    print("[smoke] Missing required API methods:")
    for method, path in missing_methods:
        print(f"  - {method} {path}")
    raise SystemExit(1)

print("[smoke] Required API endpoints are present.")
PY

echo "[smoke] Checking /health"
if [[ -n "${LIGHTRAG_API_KEY:-}" ]]; then
  curl -fsS -H "X-API-Key: ${LIGHTRAG_API_KEY}" "${BASE_URL}/health" >/dev/null
else
  curl -fsS "${BASE_URL}/health" >/dev/null
fi

echo "[smoke] OK"

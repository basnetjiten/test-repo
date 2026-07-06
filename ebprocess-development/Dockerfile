# syntax=docker/dockerfile:1

# ── Stage 1: Flutter + Dart CLIs ─────────────────────────────────────────────
FROM debian:bookworm-slim AS flutter-base

ARG FLUTTER_VERSION=3.41.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl unzip xz-utils zip libglu1-mesa ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${FLUTTER_VERSION} \
    https://github.com/flutter/flutter.git /opt/flutter

ENV PATH="/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:/root/.pub-cache/bin:$PATH"

# Warm up Flutter cache
RUN flutter precache --universal

# Install project-specific Dart CLIs
RUN dart pub global activate intl_utils


# ── Stage 2: Final image ──────────────────────────────────────────────────────
FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates nodejs npm unzip \
    && rm -rf /var/lib/apt/lists/*

# Copy Flutter SDK from stage 1
COPY --from=flutter-base /opt/flutter /opt/flutter
COPY --from=flutter-base /root/.pub-cache /root/.pub-cache

ENV PATH="/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:/root/.pub-cache/bin:$PATH"

# Install OpenCode client CLI globally
RUN npm install -g opencode-ai@latest

WORKDIR /app

# Install Python dependencies from pyproject.toml
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
COPY langgraph.json .

# Workspace dir for per-job repos
RUN mkdir -p /workspace/jobs
ENV WORKSPACE_DIR=/workspace/jobs

EXPOSE 8000

CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "8000"]

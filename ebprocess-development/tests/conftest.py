from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures: Config
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override env vars that would touch filesystem / real services.

    Every Settings field is cleared back to its code-default so tests
    are predictable regardless of the developer's .env / shell.
    """
    # Postgres
    monkeypatch.setenv("POSTGRES_URL", "")
    # Git
    monkeypatch.setenv("GIT_TOKEN", "")
    monkeypatch.setenv("GIT_USER", "")
    monkeypatch.setenv("GIT_USER_EMAIL", "bot@ebprocess.local")
    monkeypatch.setenv("GITHUB_USER", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("BITBUCKET_USERNAME", "")
    monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "")
    monkeypatch.setenv("BITBUCKET_REPO_URL", "")
    # Provider keys
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_GENERATIVE_AI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("FIGMA_PAT", "")
    # OpenCode
    monkeypatch.setenv("OPENCODE_BIN", "opencode")
    monkeypatch.setenv("OPENCODE_SERVER_URL", "")
    monkeypatch.setenv("OPENCODE_API_KEY", "")
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    monkeypatch.setenv("OPENCODE_PROJECT_DIR", "/tmp/ebtest/opencode")
    # Workspace
    monkeypatch.setenv("WORKSPACE_DIR", "/tmp/ebtest/workspace")
    # Pipeline tuning
    monkeypatch.setenv("MAX_REPAIR_ITERATIONS", "3")
    monkeypatch.setenv("MAX_RETRIES", "3")
    monkeypatch.setenv("RETRY_BACKOFF_BASE", "2")
    monkeypatch.setenv("CHECKPOINT_CLEANUP_ON_COMPLETE", "true")
    # Rate limiting & circuit breaker
    monkeypatch.setenv("RATE_LIMIT_MAX_CONCURRENT", "4")
    monkeypatch.setenv("RATE_LIMIT_PER_SECOND", "0.0")
    monkeypatch.setenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")
    monkeypatch.setenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30.0")
    # Starterkit paths
    monkeypatch.setenv("STARTERKIT_API_PATH", "/tmp/ebtest/starterkit/ebthemes-api")
    monkeypatch.setenv("STARTERKIT_FLUTTER_PATH", "/tmp/ebtest/starterkit/flutterkit")
    # Default repo
    monkeypatch.setenv("DEFAULT_PROJECT_REPO", "https://bitbucket.org/test/test-repo.git")
    # API bases
    monkeypatch.setenv("BITBUCKET_API_BASE", "https://api.bitbucket.org/2.0")
    monkeypatch.setenv("GITHUB_API_BASE", "https://api.github.com")
    # OpenCode default URL
    monkeypatch.setenv("OPENCODE_SERVER_DEFAULT_URL", "http://opencode:4096")
    # API server
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "8000")
    # Sentry
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setenv("SENTRY_ENV", "development")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")


@pytest.fixture
def opencode_project_dir() -> Path:
    return Path("/tmp/ebtest/opencode")


@pytest.fixture
def workspace_dir() -> Path:
    return Path("/tmp/ebtest/workspace")


# ---------------------------------------------------------------------------
# Fixtures: HTTP mocks
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_httpx_client() -> MagicMock:
    """Return a mock that can replace httpx.AsyncClient."""
    client = MagicMock()
    client.request = AsyncMock()
    client.stream = MagicMock()
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Fixtures: Async helpers
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def sample_async_gen() -> AsyncGenerator[int, None]:
    """Simple async generator for testing."""
    for i in range(3):
        yield i

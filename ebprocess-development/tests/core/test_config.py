from __future__ import annotations

from pathlib import Path

import pytest

from ebdev.config import Settings


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.POSTGRES_URL == ""
        assert s.MAX_REPAIR_ITERATIONS == 3
        assert s.MAX_RETRIES == 3
        assert s.RETRY_BACKOFF_BASE == 2
        assert s.CHECKPOINT_CLEANUP_ON_COMPLETE is True

    def test_default_repo(self) -> None:
        s = Settings()
        assert "bitbucket" in s.DEFAULT_PROJECT_REPO

    def test_api_bases(self) -> None:
        s = Settings()
        assert "bitbucket" in s.BITBUCKET_API_BASE
        assert "github" in s.GITHUB_API_BASE

    def test_opencode_defaults(self) -> None:
        s = Settings()
        assert "opencode" in s.OPENCODE_SERVER_DEFAULT_URL
        assert "4096" in s.OPENCODE_SERVER_DEFAULT_URL
        assert s.OPENCODE_MODEL == "claude-3-5-sonnet"

    def test_starterkit_paths(self) -> None:
        s = Settings()
        assert "starterkit" in s.STARTERKIT_API_PATH
        assert "starterkit" in s.STARTERKIT_FLUTTER_PATH

    def test_api_server_defaults(self) -> None:
        s = Settings()
        assert s.API_HOST == "0.0.0.0"
        assert s.API_PORT == 8000

    def test_sentry_defaults(self) -> None:
        s = Settings()
        assert s.SENTRY_DSN == ""
        assert s.SENTRY_ENV == "development"
        assert s.SENTRY_TRACES_SAMPLE_RATE == 0.1

    def test_rate_limiting_defaults(self) -> None:
        s = Settings()
        assert s.RATE_LIMIT_MAX_CONCURRENT == 4
        assert s.RATE_LIMIT_PER_SECOND == 0.0

    def test_circuit_breaker_defaults(self) -> None:
        s = Settings()
        assert s.CIRCUIT_BREAKER_FAILURE_THRESHOLD == 5
        assert s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT == 30.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_REPAIR_ITERATIONS", "10")
        monkeypatch.setenv("RATE_LIMIT_MAX_CONCURRENT", "20")
        s = Settings()
        assert s.MAX_REPAIR_ITERATIONS == 10
        assert s.RATE_LIMIT_MAX_CONCURRENT == 20

    def test_boolean_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHECKPOINT_CLEANUP_ON_COMPLETE", "false")
        s = Settings()
        assert s.CHECKPOINT_CLEANUP_ON_COMPLETE is False

    def test_float_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
        s = Settings()
        assert s.SENTRY_TRACES_SAMPLE_RATE == 0.5

    def test_project_root(self) -> None:
        root = Path(__file__).parent.parent.parent / "src"
        assert (root / "ebdev").is_dir()

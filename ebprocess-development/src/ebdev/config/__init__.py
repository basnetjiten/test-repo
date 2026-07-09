from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(find_dotenv())

PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Postgres (checkpointer)
    # ------------------------------------------------------------------
    POSTGRES_URL: str = Field(default="", validation_alias="POSTGRES_URL")

    # ------------------------------------------------------------------
    # Git credentials
    # ------------------------------------------------------------------
    GIT_TOKEN: str = Field(default="", validation_alias="GIT_TOKEN")
    GIT_USER: str = Field(default="", validation_alias="GIT_USER")
    GIT_USER_EMAIL: str = Field(default="bot@ebprocess.local", validation_alias="GIT_USER_EMAIL")

    # GitHub credentials
    GITHUB_USER: str = Field(default="", validation_alias="GITHUB_USER")
    GITHUB_TOKEN: str = Field(default="", validation_alias="GITHUB_TOKEN")

    # Bitbucket credentials
    BITBUCKET_USERNAME: str = Field(default="", validation_alias="BITBUCKET_USERNAME")
    BITBUCKET_APP_PASSWORD: str = Field(default="", validation_alias="BITBUCKET_APP_PASSWORD")
    BITBUCKET_REPO_URL: str = Field(default="", validation_alias="BITBUCKET_REPO_URL")

    # ------------------------------------------------------------------
    # Provider API keys
    # ------------------------------------------------------------------
    ANTHROPIC_API_KEY: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    OPENAI_API_KEY: str = Field(default="", validation_alias="OPENAI_API_KEY")
    GOOGLE_GENERATIVE_AI_API_KEY: str = Field(default="", validation_alias="GOOGLE_GENERATIVE_AI_API_KEY")
    GROQ_API_KEY: str = Field(default="", validation_alias="GROQ_API_KEY")
    FIGMA_PAT: str = Field(default="", validation_alias="FIGMA_PAT")

    # ------------------------------------------------------------------
    # OpenCode server & mode
    # ------------------------------------------------------------------
    OPENCODE_BIN: str = Field(default="opencode", validation_alias="OPENCODE_BIN")
    OPENCODE_SERVER_URL: str = Field(default="", validation_alias="OPENCODE_SERVER_URL")
    OPENCODE_API_KEY: str = Field(default="", validation_alias="OPENCODE_API_KEY")
    OPENCODE_MODEL: str = Field(default="claude-3-5-sonnet", validation_alias="OPENCODE_MODEL")
    OPENCODE_PROJECT_DIR: str = Field(
        default=str((PROJECT_ROOT / ".opencode").resolve()),
        validation_alias="OPENCODE_PROJECT_DIR",
    )

    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------
    WORKSPACE_DIR: str = Field(
        default=str((PROJECT_ROOT / "workspace").resolve()),
        validation_alias="WORKSPACE_DIR",
    )

    # ------------------------------------------------------------------
    # Pipeline tuning
    # ------------------------------------------------------------------
    MAX_REPAIR_ITERATIONS: int = Field(default=3, validation_alias="MAX_REPAIR_ITERATIONS")
    MAX_RETRIES: int = Field(default=3, validation_alias="MAX_RETRIES")
    RETRY_BACKOFF_BASE: int = Field(default=2, validation_alias="RETRY_BACKOFF_BASE")
    CHECKPOINT_CLEANUP_ON_COMPLETE: bool = Field(default=True, validation_alias="CHECKPOINT_CLEANUP_ON_COMPLETE")

    # ------------------------------------------------------------------
    # Rate limiting & circuit breaker
    # ------------------------------------------------------------------
    RATE_LIMIT_MAX_CONCURRENT: int = Field(default=4, validation_alias="RATE_LIMIT_MAX_CONCURRENT")
    RATE_LIMIT_PER_SECOND: float = Field(default=0.0, validation_alias="RATE_LIMIT_PER_SECOND")
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=5, validation_alias="CIRCUIT_BREAKER_FAILURE_THRESHOLD")
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: float = Field(default=30.0, validation_alias="CIRCUIT_BREAKER_RECOVERY_TIMEOUT")

    # ------------------------------------------------------------------
    # Starter-kit source paths (local overrides; replace with git URLs in prod)
    # ------------------------------------------------------------------
    STARTERKIT_API_PATH: str = Field(
        default="/Users/ebpearls/Desktop/starterkit/ebthemes-api",
        validation_alias="STARTERKIT_API_PATH",
    )
    STARTERKIT_FLUTTER_PATH: str = Field(
        default="/Users/ebpearls/Desktop/starterkit/flutterkit",
        validation_alias="STARTERKIT_FLUTTER_PATH",
    )

    # ------------------------------------------------------------------
    # Default project repo (overridable per request)
    # ------------------------------------------------------------------
    DEFAULT_PROJECT_REPO: str = Field(
        default="https://bitbucket.org/basnetjiten7/test-repo.git",
        validation_alias="DEFAULT_PROJECT_REPO",
    )

    # ------------------------------------------------------------------
    # Remote API bases (Bitbucket / GitHub)
    # ------------------------------------------------------------------
    BITBUCKET_API_BASE: str = Field(
        default="https://api.bitbucket.org/2.0",
        validation_alias="BITBUCKET_API_BASE",
    )
    GITHUB_API_BASE: str = Field(
        default="https://api.github.com",
        validation_alias="GITHUB_API_BASE",
    )

    # ------------------------------------------------------------------
    # OpenCode server default URL (fallback)
    # ------------------------------------------------------------------
    OPENCODE_SERVER_DEFAULT_URL: str = Field(
        default="http://opencode:4096",
        validation_alias="OPENCODE_SERVER_DEFAULT_URL",
    )

    # ------------------------------------------------------------------
    # API server (FastAPI)
    # ------------------------------------------------------------------
    API_HOST: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    API_PORT: int = Field(default=8000, validation_alias="API_PORT")

    # ------------------------------------------------------------------
    # Sentry
    # ------------------------------------------------------------------
    SENTRY_DSN: str = Field(default="", validation_alias="SENTRY_DSN")
    SENTRY_ENV: str = Field(default="development", validation_alias="SENTRY_ENV")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.1, validation_alias="SENTRY_TRACES_SAMPLE_RATE")

    # ------------------------------------------------------------------
    # OpenTelemetry
    # ------------------------------------------------------------------
    OTLP_ENDPOINT: str = Field(default="", validation_alias="OTLP_ENDPOINT")


config = Settings()

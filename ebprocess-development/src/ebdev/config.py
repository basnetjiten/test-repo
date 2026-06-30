# -*- coding: utf-8 -*-
"""Environment-based configuration management for ebprocess-development."""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.resolve()


class Config:
    """Project configuration loaded from environment variables."""

    POSTGRES_URL: str = os.environ.get("POSTGRES_URL", "")

    GIT_TOKEN: str = os.environ.get("GIT_TOKEN", "")
    GIT_USER: str = os.environ.get("GIT_USER", "")
    GIT_USER_EMAIL: str = os.environ.get("GIT_USER_EMAIL", "bot@ebprocess.local")

    # GitHub credentials
    GITHUB_USER: str = os.environ.get("GITHUB_USER", "")
    GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

    # Bitbucket credentials
    BITBUCKET_USERNAME: str = os.environ.get("BITBUCKET_USERNAME", "")
    BITBUCKET_APP_PASSWORD: str = os.environ.get("BITBUCKET_APP_PASSWORD", "")
    BITBUCKET_REPO_URL: str = os.environ.get("BITBUCKET_REPO_URL", "")

    # OpenCode executable & API keys
    OPENCODE_BIN: str = os.environ.get("OPENCODE_BIN", "opencode")
    OPENCODE_API_KEY: str = os.environ.get("OPENCODE_API_KEY", "")
    OPENCODE_MODEL: str = os.environ.get("OPENCODE_MODEL", "claude-3-5-sonnet")
    OPENCODE_PROJECT_DIR: str = os.environ.get(
        "OPENCODE_PROJECT_DIR",
        str((PROJECT_ROOT / ".opencode").resolve()),
    )

    # Provider Keys
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    GOOGLE_GENERATIVE_AI_API_KEY: str = os.environ.get(
        "GOOGLE_GENERATIVE_AI_API_KEY", ""
    )
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")

    # Figma Personal Access Token
    FIGMA_PAT: str = os.environ.get("FIGMA_PAT", "")

    WORKSPACE_DIR: str = os.environ.get(
        "WORKSPACE_DIR",
        str(Path("/Users/ebpearls/Desktop/macmini-migrated/CascadeProjects").resolve()),
    )
    MAX_REPAIR_ITERATIONS: int = int(os.environ.get("MAX_REPAIR_ITERATIONS", "3"))

    # Sentry Configuration
    SENTRY_DSN: str = os.environ.get("SENTRY_DSN", "")
    SENTRY_ENV: str = os.environ.get("SENTRY_ENV", "development")
    SENTRY_TRACES_SAMPLE_RATE: float = float(
        os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
    )


config = Config()

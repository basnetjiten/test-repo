# -*- coding: utf-8 -*-
"""Custom domain exceptions for ebprocess-development."""

from __future__ import annotations


class EbDevError(Exception):
    """Base exception for all domain errors within ebprocess-development."""


class OpenCodeExecutionError(EbDevError):
    """Raised when communication, sessions, or prompt execution on OpenCode server fails."""


class GitServiceError(EbDevError):
    """Raised when repository cloning, remote checking, or git sync operations fail."""


class PlatformStrategyError(EbDevError):
    """Raised when platform strategy setup, validation, or bootstrap execution fails."""


class UnsupportedPlatformError(PlatformStrategyError, ValueError):
    """Raised when an unsupported platform name is requested from the factory."""


class EpicStateError(EbDevError):
    """Raised when reading or writing the .ebpearls/Epic-{id}/state.json artifact registry fails."""


class OrchestrationError(EbDevError):
    """Raised when the dynamic orchestration strategy generation fails or is invalid."""

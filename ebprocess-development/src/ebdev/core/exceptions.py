# -*- coding: utf-8 -*-
"""Custom domain exceptions for ebprocess-development."""

from __future__ import annotations


class EbDevError(Exception):
    """Base exception for all domain errors within ebprocess-development."""
    pass


class OpenCodeExecutionError(EbDevError):
    """Raised when communication, sessions, or prompt execution on OpenCode server fails."""
    pass


class GitServiceError(EbDevError):
    """Raised when repository cloning, remote checking, or git sync operations fail."""
    pass


class PlatformStrategyError(EbDevError):
    """Raised when platform strategy setup, validation, or bootstrap execution fails."""
    pass


class UnsupportedPlatformError(PlatformStrategyError, ValueError):
    """Raised when an unsupported platform name is requested from the factory."""
    pass

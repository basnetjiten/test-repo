# -*- coding: utf-8 -*-
"""Abstract base class for platform execution strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PlatformStrategy(ABC):
    """Abstract Strategy interface for platform-specific build, lint, and test actions."""

    @abstractmethod
    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """Resolve platform-specific dependencies and environment setup."""
        pass

    @abstractmethod
    async def validate(self, repo_path: Path) -> list[str]:
        """Execute platform lint, compilation, and test checks. Returns list of errors."""
        pass

    @abstractmethod
    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """Seed a brand-new workspace project skeleton for the platform."""
        pass

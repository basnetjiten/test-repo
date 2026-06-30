# -*- coding: utf-8 -*-
"""Concrete execution strategy for CMS platforms."""

from __future__ import annotations

from pathlib import Path

from ebdev.platforms.base import PlatformStrategy
from ebdev.core.logger import get_logger

logger = get_logger(__name__)


class CmsStrategy(PlatformStrategy):
    """Placeholder strategy handling linter, dependency and test actions for CMS platforms."""

    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        logger.info(f"Preparing CMS workspace at {repo_path} (No-Op placeholder)")
        pass

    async def validate(self, repo_path: Path) -> list[str]:
        logger.info(f"Validating CMS workspace at {repo_path} (No-Op placeholder)")
        return []

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        logger.info(f"Bootstrapping CMS workspace at {repo_path} (No-Op placeholder)")
        pass

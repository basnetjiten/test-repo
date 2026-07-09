# -*- coding: utf-8 -*-
"""
web.py
======
Concrete execution strategy for Web platforms (React / Next.js).

Responsibilities
----------------
* Placeholder strategy for dependency resolution, validation, and scaffolding on web platforms.
"""

from __future__ import annotations

from pathlib import Path

from ebdev.core.logger import get_logger
from ebdev.platforms.base import PlatformStrategy

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Web Platform Strategy
# ---------------------------------------------------------------------------
class WebStrategy(PlatformStrategy):
    """Placeholder strategy handling linter, dependency, and test actions for web client frameworks."""

    async def prepare(self, repo_path: Path, _branch_name: str) -> None:
        """
        Resolve Web project dependencies (placeholder).

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch.
        """
        logger.info("Preparing Web workspace at %s (No-Op placeholder)", repo_path)
        pass

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Validate Web workspace layout and linting (placeholder).

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of validation error messages. Empty for this placeholder.
        """
        logger.info("Validating Web workspace at %s (No-Op placeholder)", repo_path)
        return []

    async def bootstrap(self, _repo_path: Path, _starter_type: str) -> None:
        """
        Bootstrap Web client boilerplate (placeholder).

        Parameters
        ----------
        _repo_path : Path
            The destination repository directory.
        _starter_type : str
            The type of starter skeleton to bootstrap.
        """
        logger.info("Bootstrapping Web workspace (No-Op placeholder)")
        pass

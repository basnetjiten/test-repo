# -*- coding: utf-8 -*-
"""
cms.py
======
Concrete execution strategy for CMS platforms.

Responsibilities
----------------
* Placeholder handling for linter, dependency, and test actions on CMS platforms.
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
# CMS Platform Strategy
# ---------------------------------------------------------------------------
class CmsStrategy(PlatformStrategy):
    """Placeholder strategy handling linter, dependency, and test actions for CMS platforms."""

    async def prepare(self, repo_path: Path, _branch_name: str) -> None:
        """
        Resolve CMS dependencies (placeholder).

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch.
        """
        logger.info("Preparing CMS workspace at %s (No-Op placeholder)", repo_path)
        pass

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Validate CMS workspace layout and linting (placeholder).

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of validation error messages. Empty for this placeholder.
        """
        logger.info("Validating CMS workspace at %s (No-Op placeholder)", repo_path)
        return []

    async def bootstrap(self, repo_path: Path, _starter_type: str) -> None:
        """
        Bootstrap CMS files and directories (placeholder).

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap.
        """
        logger.info("Bootstrapping CMS workspace at %s (No-Op placeholder)", repo_path)
        pass

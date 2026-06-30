# -*- coding: utf-8 -*-
"""
base.py
=======
Abstract base class for platform execution strategies.

Responsibilities
----------------
* Outline requirements for concrete build and test pipeline integrations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PlatformStrategy(ABC):
    """Abstract Strategy interface for platform-specific build, lint, and test actions."""

    @abstractmethod
    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """
        Resolve platform-specific dependencies and environment setup.

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch to set up.
        """
        pass

    @abstractmethod
    async def validate(self, repo_path: Path) -> list[str]:
        """
        Execute platform lint, compilation, and test checks.

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of error messages. Empty if validation succeeds.
        """
        pass

    @abstractmethod
    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """
        Seed a brand-new workspace project skeleton for the platform.

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap.
        """
        pass

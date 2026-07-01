# -*- coding: utf-8 -*-
"""
flutter.py
==========
Concrete execution strategy for Flutter platforms.

Responsibilities
----------------
* Resolve Flutter project packages via pub get.
* Run Flutter/Dart analysis and build runner code generation tasks.
* Scaffold and bootstrap empty or templated Flutter project structures.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ebdev.core.exceptions import PlatformStrategyError
from ebdev.platforms.base import PlatformStrategy
from ebdev.services import flutter_cmd

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flutter Platform Strategy
# ---------------------------------------------------------------------------
class FlutterStrategy(PlatformStrategy):
    """Execution strategy handling linter, dependency, and scaffolding actions for Flutter mobile."""

    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """
        Resolve Flutter dependencies.

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch.

        Raises
        ------
        PlatformStrategyError
            If running flutter pub get fails.
        """
        logger.info("Preparing Flutter repository at %s", repo_path)
        output_lines: list[str] = []
        if not await flutter_cmd.pub_get(str(repo_path), output=output_lines):
            err_msg = output_lines[-1] if output_lines else "flutter pub get failed"
            raise PlatformStrategyError(f"Flutter preparation failure: {err_msg}")

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Run pub get, build_runner, and analyze. Fail only on lint errors.

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of validation error messages. Empty if validation passes.
        """
        logger.info("Validating Flutter repository at %s", repo_path)
        output_lines: list[str] = []
        errors: list[str] = []

        # 1. Resolve dependencies
        await flutter_cmd.pub_get(str(repo_path), output=output_lines)

        # 2. Run build_runner code-generation
        await flutter_cmd.build_runner(str(repo_path), output=output_lines)

        # 3. Static analysis linter
        analyze_ok = await flutter_cmd.analyze(str(repo_path), output=output_lines)
        validation_output = "\n".join(output_lines)

        has_errors = "error •" in validation_output.lower()
        if not analyze_ok and not has_errors:
            logger.info("Flutter analyze failed but no lint errors found. Treating as PASS.")
            analyze_ok = True

        if not analyze_ok or has_errors:
            errors = [line.strip() for line in output_lines if "error •" in line.lower()]
            if not errors:
                errors = ["Flutter analysis failed with errors."]

        return errors

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """
        Scaffold brand-new Flutter project.

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap.

        Raises
        ------
        PlatformStrategyError
            If scaffolding the project template fails.
        """
        logger.info("Bootstrapping Flutter template in %s", repo_path)
        output_lines: list[str] = []

        if not await flutter_cmd.create(str(repo_path), output=output_lines):
            raise PlatformStrategyError("Failed to scaffold Flutter project template.")

        # Sync/bootstrap actions
        await flutter_cmd.pub_get(str(repo_path), output=output_lines)
        await flutter_cmd.build_runner(str(repo_path), output=output_lines)

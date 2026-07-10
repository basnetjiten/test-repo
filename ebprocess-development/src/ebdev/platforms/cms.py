# -*- coding: utf-8 -*-
"""
cms.py
======
Concrete execution strategy for CMS platforms (Vite / React).

Responsibilities
----------------
* Resolve dependency installation via npm.
* Run validation suites (eslint) on changed files.
* Execute builds to verify integrity.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from ebdev.core.exceptions import PlatformStrategyError
from ebdev.core.logger import get_logger
from ebdev.platforms.base import PlatformStrategy
from ebdev.services.fs import AsyncFileSystemService

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# CMS Platform Strategy
# ---------------------------------------------------------------------------
class CmsStrategy(PlatformStrategy):
    """Execution strategy handling linting, dependencies, and tests for CMS client frameworks (Vite/React)."""

    async def _run_command(self, cmd: list[str], cwd: Path) -> tuple[int, bytes, bytes]:
        """
        Execute a subprocess command in the given working directory.
        """
        env = os.environ.copy()
        env["ESLINT_USE_FLAT_CONFIG"] = "false"
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode if proc.returncode is not None else -1, stdout, stderr

    async def prepare(self, repo_path: Path, _branch_name: str) -> None:
        """
        Resolve CMS project dependencies.
        """
        logger.info("Preparing CMS repository at %s", repo_path)
        package_json = repo_path / "package.json"

        if await AsyncFileSystemService.exists(package_json):
            logger.info("Detected Vite/React project. Installing node modules...")
            returncode, stdout, stderr = await self._run_command(
                ["npm", "install", "--legacy-peer-deps", "--engine-strict=false"], repo_path
            )
            if returncode != 0:
                logger.debug(
                    "npm install returned non-zero (may be a devDep/peer-dep warning). "
                    "stderr: %s | stdout: %s",
                    stderr.decode().strip(),
                    stdout.decode().strip(),
                )
            else:
                logger.info("Vite/React dependencies installed successfully.")

            # Rebuild native packages to resolve host-container architecture mismatch
            logger.info("Rebuilding native dependencies inside container...")
            rb_code, rb_out, rb_err = await self._run_command(["npm", "rebuild"], repo_path)
            if rb_code != 0:
                logger.warning(
                    "npm rebuild returned non-zero. stderr: %s",
                    rb_err.decode().strip(),
                )
            else:
                logger.info("Native dependencies rebuilt successfully.")

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Validate CMS workspace layout and linting.
        """
        logger.info("Validating CMS repository at %s", repo_path)
        package_json = repo_path / "package.json"
        errors: list[str] = []

        if await AsyncFileSystemService.exists(package_json):
            logger.info("Identifying modified and untracked TypeScript/TSX files in CMS workspace...")
            rc, stdout, _stderr = await self._run_command(["git", "status", "--porcelain"], repo_path)
            changed_files: list[str] = []
            if rc == 0:
                for line in stdout.decode("utf-8").splitlines():
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2:
                        _status, filepath = parts
                        filepath = filepath.strip()
                        full_path = repo_path / filepath
                        if full_path.is_dir():
                            for p in full_path.glob("**/*.ts"):
                                changed_files.append(str(p.relative_to(repo_path)))
                            for p in full_path.glob("**/*.tsx"):
                                changed_files.append(str(p.relative_to(repo_path)))
                        elif filepath.endswith(".ts") or filepath.endswith(".tsx"):
                            changed_files.append(filepath)

            if changed_files:
                logger.info("Running Vite/React linting on %d modified files...", len(changed_files))
                cmd = ["npx", "eslint", "--fix", *changed_files]
                returncode, out, err = await self._run_command(cmd, repo_path)

                lint_output = out.decode("utf-8", errors="replace") + err.decode("utf-8", errors="replace")
                if "error" in lint_output.lower() or returncode != 0:
                    for line in lint_output.splitlines():
                        if "error" in line.lower() and ":" in line:
                            errors.append(line.strip())
                    if not errors:
                        errors.append("Linting failed with error(s).")
            else:
                logger.info("No changed TS/TSX files detected. Skipping linting.")

            try:
                pkg_data = await AsyncFileSystemService.read_json(package_json)
                scripts = pkg_data.get("scripts", {})
                if "test" in scripts:
                    logger.info("Running CMS tests...")
                    returncode, _, err = await self._run_command(["npm", "run", "test", "--", "--passWithNoTests"], repo_path)
                    if returncode != 0:
                        errors.append(f"CMS tests failed: {err.decode().strip()}")
            except Exception as exc:
                logger.debug("Failed to read package.json scripts for testing: %s", exc)
        return errors

    async def bootstrap(self, _repo_path: Path, starter_type: str) -> None:
        """
        Bootstrap CMS client boilerplate.
        """
        raise PlatformStrategyError(
            f"Bootstrapping new boilerplate for {starter_type} is disabled. "
            "The repository must be pre-populated or cloned from a starter kit."
        )

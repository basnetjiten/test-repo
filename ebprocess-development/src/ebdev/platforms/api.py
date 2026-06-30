# -*- coding: utf-8 -*-
"""Concrete execution strategy for Python API platforms."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ebdev.platforms.base import PlatformStrategy
from ebdev.core.logger import get_logger

logger = get_logger(__name__)


class ApiStrategy(PlatformStrategy):
    """Execution strategy handling linting, dependencies, and tests for python backend projects."""

    async def _run_command(self, cmd: list[str], cwd: Path) -> tuple[int, bytes, bytes]:
        """Execute a subprocess command in the given working directory."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout, stderr

    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """Install python dependency requirements."""
        logger.info(f"Preparing Python API repository at {repo_path}")
        req_txt = repo_path / "requirements.txt"
        pyproj = repo_path / "pyproject.toml"

        if req_txt.exists():
            returncode, _, stderr = await self._run_command(
                ["pip", "install", "-r", "requirements.txt"], repo_path
            )
            if returncode != 0:
                logger.warning(f"pip install requirements failed: {stderr.decode().strip()}")
        elif pyproj.exists():
            returncode, _, stderr = await self._run_command(
                ["pip", "install", "-e", "."], repo_path
            )
            if returncode != 0:
                logger.warning(f"pip install editable package failed: {stderr.decode().strip()}")

    async def validate(self, repo_path: Path) -> list[str]:
        """Execute Python linters (ruff/flake8) and test suites (pytest/unittest)."""
        logger.info(f"Validating Python API repository at {repo_path}")
        errors: list[str] = []

        # 1. Run Lint check
        lint_ok = True
        lint_tool = None
        for tool in ["ruff", "flake8"]:
            if shutil.which(tool):
                lint_tool = tool
                cmd = [tool, "check", "."] if tool == "ruff" else [tool, "."]
                returncode, _, stderr = await self._run_command(cmd, repo_path)
                lint_ok = (returncode == 0)
                if not lint_ok:
                    logger.warning(f"API Linting with {tool} failed: {stderr.decode().strip()}")
                break

        # 2. Run Test check
        test_ok = True
        test_tool = None
        if (repo_path / "tests").exists() or (repo_path / "pytest.ini").exists():
            for tool in ["pytest", "unittest"]:
                if shutil.which(tool):
                    test_tool = tool
                    cmd = [tool] if tool == "pytest" else ["python3", "-m", "unittest", "discover"]
                    returncode, _, stderr = await self._run_command(cmd, repo_path)
                    test_ok = (returncode == 0)
                    if not test_ok:
                        logger.warning(f"API Testing with {tool} failed: {stderr.decode().strip()}")
                    break

        if not lint_ok:
            errors.append(f"API Linting failed using {lint_tool or 'linter'}.")
        if not test_ok:
            errors.append(f"API Testing failed using {test_tool or 'test runner'}.")

        return errors

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """Seed API project files (e.g. pyproject.toml, standard folders)."""
        logger.info(f"Bootstrapping Python API boilerplate in {repo_path}")
        # Scaffolds basic main and tests layout
        (repo_path / "app").mkdir(parents=True, exist_ok=True)
        (repo_path / "tests").mkdir(parents=True, exist_ok=True)
        
        main_py = repo_path / "app" / "main.py"
        if not main_py.exists():
            main_py.write_text(
                'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/")\ndef read_root():\n    return {"Hello": "World"}\n',
                encoding="utf-8"
            )

        test_py = repo_path / "tests" / "test_main.py"
        if not test_py.exists():
            test_py.write_text(
                'def test_root():\n    assert True\n',
                encoding="utf-8"
            )

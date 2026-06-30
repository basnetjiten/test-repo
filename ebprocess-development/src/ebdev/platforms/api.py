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

    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """Install python dependency requirements."""
        logger.info(f"Preparing Python API repository at {repo_path}")
        req_txt = repo_path / "requirements.txt"
        pyproj = repo_path / "pyproject.toml"

        if req_txt.exists():
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "-r", "requirements.txt",
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"pip install requirements failed: {stderr.decode()}")
        elif pyproj.exists():
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "-e", ".",
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"pip install editable package failed: {stderr.decode()}")

    async def validate(self, repo_path: Path) -> list[str]:
        """Execute Python linters (ruff/flake8) and test suites (pytest/unittest)."""
        logger.info(f"Validating Python API repository at {repo_path}")
        errors: list[str] = []

        # 1. Run Lint check
        lint_ok = True
        lint_tool = None
        if shutil.which("ruff"):
            lint_tool = "ruff"
            proc = await asyncio.create_subprocess_exec(
                "ruff", "check", ".",
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            lint_ok = (proc.returncode == 0)
        elif shutil.which("flake8"):
            lint_tool = "flake8"
            proc = await asyncio.create_subprocess_exec(
                "flake8", ".",
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            lint_ok = (proc.returncode == 0)

        # 2. Run Test check
        test_ok = True
        test_tool = None
        if (repo_path / "tests").exists() or (repo_path / "pytest.ini").exists():
            if shutil.which("pytest"):
                test_tool = "pytest"
                proc = await asyncio.create_subprocess_exec(
                    "pytest",
                    cwd=str(repo_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                test_ok = (proc.returncode == 0)
            else:
                test_tool = "unittest"
                proc = await asyncio.create_subprocess_exec(
                    "python3", "-m", "unittest", "discover",
                    cwd=str(repo_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                test_ok = (proc.returncode == 0)

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

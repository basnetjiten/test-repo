# -*- coding: utf-8 -*-
"""
api.py
======
Concrete execution strategy for Python and NestJS/Node API platforms.

Responsibilities
----------------
* Resolve package dependencies (pip or npm).
* Run validation suites (ruff/flake8 for Python, npm lint/test for NestJS).
* Bootstrap skeleton backends (FastAPI app layouts or basic NestJS folders).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from ebdev.platforms.base import PlatformStrategy

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API Platform Strategy
# ---------------------------------------------------------------------------
class ApiStrategy(PlatformStrategy):
    """Execution strategy handling linting, dependencies, and tests for API/backend projects (Python and NestJS/Node)."""

    async def _run_command(self, cmd: list[str], cwd: Path) -> tuple[int, bytes, bytes]:
        """
        Execute a subprocess command in the given working directory.

        Parameters
        ----------
        cmd : list[str]
            The list of command and arguments.
        cwd : Path
            The working directory.

        Returns
        -------
        tuple[int, bytes, bytes]
            A tuple containing return code, stdout bytes, and stderr bytes.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode if proc.returncode is not None else -1, stdout, stderr

    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """
        Install package/repository dependencies.

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch.
        """
        logger.info("Preparing API repository at %s", repo_path)
        package_json = repo_path / "package.json"
        req_txt = repo_path / "requirements.txt"
        pyproj = repo_path / "pyproject.toml"

        if package_json.exists():
            # Node / NestJS
            logger.info("Detected NestJS/Node project. Installing node modules...")
            # Try npm install
            returncode, _, stderr = await self._run_command(["npm", "install"], repo_path)
            if returncode != 0:
                logger.warning("npm install failed: %s", stderr.decode().strip())
        elif req_txt.exists():
            # Python requirements.txt
            logger.info("Detected Python (requirements.txt) project. Installing dependencies...")
            returncode, _, stderr = await self._run_command(
                ["pip", "install", "-r", "requirements.txt"], repo_path
            )
            if returncode != 0:
                logger.warning("pip install requirements failed: %s", stderr.decode().strip())
        elif pyproj.exists():
            # Python pyproject.toml
            logger.info("Detected Python (pyproject.toml) project. Installing editable package...")
            returncode, _, stderr = await self._run_command(
                ["pip", "install", "-e", "."], repo_path
            )
            if returncode != 0:
                logger.warning("pip install editable package failed: %s", stderr.decode().strip())

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Execute project-specific linters and test suites.

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of validation error messages. Empty if validation passes.
        """
        logger.info("Validating API repository at %s", repo_path)
        package_json = repo_path / "package.json"
        errors: list[str] = []

        if package_json.exists():
            # Node / NestJS validation
            lint_ok = True
            test_ok = True
            
            # Check npm run lint
            logger.info("Running NestJS linting...")
            returncode, _, stderr = await self._run_command(["npm", "run", "lint"], repo_path)
            if returncode != 0:
                lint_ok = False
                logger.warning("NestJS Linting failed: %s", stderr.decode().strip())

            # Check npm run test
            logger.info("Running NestJS tests...")
            returncode, _, stderr = await self._run_command(["npm", "run", "test"], repo_path)
            if returncode != 0:
                test_ok = False
                logger.warning("NestJS Testing failed: %s", stderr.decode().strip())

            if not lint_ok:
                errors.append("API Linting failed using npm run lint.")
            if not test_ok:
                errors.append("API Testing failed using npm run test.")
        else:
            # Python Validation
            lint_ok = True
            lint_tool = None
            for tool in ["ruff", "flake8"]:
                if shutil.which(tool):
                    lint_tool = tool
                    cmd = [tool, "check", "."] if tool == "ruff" else [tool, "."]
                    returncode, _, stderr = await self._run_command(cmd, repo_path)
                    lint_ok = (returncode == 0)
                    if not lint_ok:
                        logger.warning("API Linting with %s failed: %s", tool, stderr.decode().strip())
                    break

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
                            logger.warning("API Testing with %s failed: %s", tool, stderr.decode().strip())
                        break

            if not lint_ok:
                errors.append(f"API Linting failed using {lint_tool or 'linter'}.")
            if not test_ok:
                errors.append(f"API Testing failed using {test_tool or 'test runner'}.")

        return errors

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """
        Seed API project files (e.g. package.json or pyproject.toml, standard folders).

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap ("nestjs", "node", etc.).
        """
        logger.info("Bootstrapping API boilerplate in %s", repo_path)
        
        if starter_type == "nestjs" or starter_type == "node":
            # Scaffolds basic NestJS/Node workspace
            (repo_path / "apps" / "api" / "src").mkdir(parents=True, exist_ok=True)
            (repo_path / "libs" / "data-access" / "src").mkdir(parents=True, exist_ok=True)
            
            package_json = repo_path / "package.json"
            if not package_json.exists():
                package_json.write_text(
                    '{\n  "name": "nestjs-api",\n  "version": "0.1.0",\n  "scripts": {\n    "lint": "echo \'Lint OK\'",\n    "test": "echo \'Test OK\'"\n  }\n}\n',
                    encoding="utf-8"
                )
        else:
            # Scaffolds basic main and tests layout (Python FastAPI)
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

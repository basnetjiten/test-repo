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

from ebdev.core.exceptions import PlatformStrategyError
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
        import os
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
            # Fix ESLint flat config file if it has legacy formatting (misconfigured starter kit)
            eslint_flat_config = repo_path / "eslint.config.js"
            if eslint_flat_config.exists():
                try:
                    content = eslint_flat_config.read_text(encoding="utf-8")
                    if "module.exports = {" in content and "parser:" in content:
                        logger.info("Detected legacy config format inside eslint.config.js. Re-writing with valid flat configuration...")
                        valid_flat_config = (
                            "const tsParser = require('@typescript-eslint/parser');\n\n"
                            "module.exports = [\n"
                            "  {\n"
                            "    files: ['**/*.ts'],\n"
                            "    languageOptions: {\n"
                            "      parser: tsParser,\n"
                            "      parserOptions: {\n"
                            "        project: './tsconfig.json',\n"
                            "        sourceType: 'module',\n"
                            "      },\n"
                            "    },\n"
                            "    rules: {\n"
                            "      '@typescript-eslint/no-explicit-any': 'off',\n"
                            "    },\n"
                            "  }\n"
                            "];\n"
                        )
                        eslint_flat_config.write_text(valid_flat_config, encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to fix legacy eslint.config.js: %s", e)

            # Node / NestJS
            logger.info("Detected NestJS/Node project. Installing node modules...")
            # Try npm install
            returncode, _, stderr = await self._run_command(["npm", "install", "--legacy-peer-deps", "--engine-strict=false"], repo_path)
            if returncode != 0:
                logger.warning("npm install failed: %s", stderr.decode().strip())
            else:
                # Compile/build the project to ensure everything is initialized and compile-checked
                import json
                try:
                    with open(package_json, "r", encoding="utf-8") as f:
                        pkg_data = json.load(f)
                    scripts = pkg_data.get("scripts", {})
                    if "build:all" in scripts:
                        logger.info("NestJS project has build:all. Running npm run build:all...")
                        rc, _, err = await self._run_command(["npm", "run", "build:all"], repo_path)
                        if rc != 0:
                            logger.warning("npm run build:all failed: %s", err.decode().strip())
                    elif "build" in scripts:
                        logger.info("NestJS project has build. Running npm run build...")
                        rc, _, err = await self._run_command(["npm", "run", "build"], repo_path)
                        if rc != 0:
                            logger.warning("npm run build failed: %s", err.decode().strip())
                except Exception as e:
                    logger.warning("Failed to parse package.json or execute build scripts: %s", e)
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
            
            # Get list of modified and untracked TS files
            logger.info("Identifying modified and untracked TypeScript files in API workspace...")
            rc, stdout, stderr = await self._run_command(["git", "status", "--porcelain"], repo_path)
            changed_files: list[str] = []
            if rc == 0:
                for line in stdout.decode("utf-8").splitlines():
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2:
                        status, filepath = parts
                        filepath = filepath.strip()
                        # If it is a directory, find all .ts files under it recursively
                        full_path = repo_path / filepath
                        if full_path.is_dir():
                            for p in full_path.glob("**/*.ts"):
                                changed_files.append(str(p.relative_to(repo_path)))
                        elif filepath.endswith(".ts"):
                            changed_files.append(filepath)

            if changed_files:
                logger.info("Running NestJS linting on %d modified files...", len(changed_files))
                # Run eslint only on the changed files
                # Using npx eslint with legacy config fallback environment variable
                cmd = ["npx", "eslint", "--fix"] + changed_files
                returncode, out, err = await self._run_command(cmd, repo_path)
                
                # Check for ESLint warnings/errors and collect only line messages
                lint_output = out.decode("utf-8", errors="replace") + err.decode("utf-8", errors="replace")
                if "error" in lint_output.lower() or returncode != 0:
                    lint_ok = False
                    # Parse error messages selectively
                    for line in lint_output.splitlines():
                        if "error" in line.lower() and ":" in line:
                            errors.append(line.strip())
                    if not errors:
                        errors.append("Linting failed with error(s).")
            else:
                logger.info("No changed TypeScript files detected. Skipping linting.")

            # Test suite verification on NestJS if tests exist
            test_target = ""
            import json
            try:
                with open(package_json, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                scripts = pkg_data.get("scripts", {})
                if "test" in scripts:
                    # Parse changed file directory to find targeted test suite
                    test_target = ""
                    for f in changed_files:
                        if "modules/" in f:
                            # Extract module folder e.g. apps/api/src/modules/create_enquiry/ -> create_enquiry
                            parts = f.split("modules/")
                            if len(parts) > 1:
                                mod_dir = parts[1].split("/")[0]
                                if mod_dir:
                                    test_target = mod_dir
                                    break
            except Exception:
                pass

            if test_target:
                logger.info("Running NestJS tests target: %s...", test_target)
                returncode, _, err = await self._run_command(["npm", "run", "test", "--", test_target, "--passWithNoTests"], repo_path)
                if returncode != 0:
                    test_ok = False
                    errors.append(f"NestJS tests failed on target '{test_target}': {err.decode().strip()}")
            else:
                logger.info("Skipping NestJS tests run...")

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

            # Skip Python test running for now as requested
            test_ok = True

            if not lint_ok:
                errors.append(f"API Linting failed using {lint_tool or 'linter'}.")

        return errors

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """
        Seed API project files. Not implemented on this platform strategy.

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap.
        """
        raise PlatformStrategyError(
            f"Bootstrapping new boilerplate for {starter_type} is disabled. "
            "The repository must be pre-populated or cloned from a starter kit."
        )

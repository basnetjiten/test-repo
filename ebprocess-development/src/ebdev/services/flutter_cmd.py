# -*- coding: utf-8 -*-
"""
flutter_cmd.py
==============
Subprocess wrappers for Flutter / Dart CLI tools using non-blocking asyncio.

Responsibilities
----------------
* Formulate environment variables for running Flutter headlessly.
* Execute async CLI commands (`flutter pub get`, `flutter analyze`, etc.) and capture standard output/error.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FLUTTER_ENV: dict[str, str] = {
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "FLUTTER_NO_ANALYTICS": "1",
}


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------
def _flutter_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """
    Construct environment dictionary for Flutter commands.

    Parameters
    ----------
    extra : dict[str, str] | None
        Extra env vars to inject.

    Returns
    -------
    dict[str, str]
        The final environment dictionary.
    """
    env = {**os.environ, **_FLUTTER_ENV}
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def run_cmd(
    cmd: list[str],
    cwd: str,
    output: Optional[list[str]] = None,
    timeout: int = 120,
    env: Optional[dict[str, str]] = None,
) -> bool:
    """
    Run a shell command asynchronously and capture output.

    Parameters
    ----------
    cmd : list[str]
        The command split into a list of words.
    cwd : str
        The working directory path to run the command in.
    output : list[str] | None
        An optional list to append the stdout/stderr of the command.
    timeout : int
        Subprocess execution timeout in seconds.
    env : dict[str, str] | None
        Optional execution environment dictionary.

    Returns
    -------
    bool
        True if the execution succeeded with return code 0.

    Raises
    ------
    TimeoutError
        If execution exceeds the timeout threshold.
    """
    resolved_env = env if env is not None else _flutter_env()
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd[0],
            *cmd[1:],
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=resolved_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Command timed out after {timeout}s") from e

        combined = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
        if output is not None:
            output.append(f"$ {' '.join(cmd)}\n{combined}")
        return proc.returncode == 0
    except Exception as e:
        if output is not None:
            output.append(f"$ {' '.join(cmd)}\nERROR: {e}")
        return False


async def pub_get(cwd: str, output: Optional[list[str]] = None) -> bool:
    """
    Run `flutter pub get`.

    Parameters
    ----------
    cwd : str
        Working directory path.
    output : list[str] | None
        Optional buffer to collect logs.

    Returns
    -------
    bool
        True if successful.
    """
    return await run_cmd(["flutter", "pub", "get"], cwd=cwd, output=output)


async def analyze(cwd: str, output: Optional[list[str]] = None) -> bool:
    """
    Run `flutter analyze`.

    Parameters
    ----------
    cwd : str
        Working directory path.
    output : list[str] | None
        Optional buffer to collect logs.

    Returns
    -------
    bool
        True if successful.
    """
    return await run_cmd(["flutter", "analyze"], cwd=cwd, output=output)


async def create(
    cwd: str,
    project_name: str = "generated_project",
    platforms: str = "android,ios",
    output: Optional[list[str]] = None,
) -> bool:
    """
    Run `flutter create` to seed a project.

    Parameters
    ----------
    cwd : str
        Working directory path.
    project_name : str
        The name of the new Flutter project.
    platforms : str
        Comma-separated list of target platform names.
    output : list[str] | None
        Optional buffer to collect logs.

    Returns
    -------
    bool
        True if successful.
    """
    cmd = [
        "flutter",
        "create",
        "--no-pub",
        "--offline",
        "--empty",
        f"--project-name={project_name}",
        f"--platforms={platforms}",
        ".",
    ]
    return await run_cmd(cmd, cwd=cwd, output=output)


async def build_runner(
    cwd: str, output: Optional[list[str]] = None, timeout: int = 300
) -> bool:
    """
    Run `dart run build_runner build --delete-conflicting-outputs`.

    Parameters
    ----------
    cwd : str
        Working directory path.
    output : list[str] | None
        Optional buffer to collect logs.
    timeout : int
        Execution timeout.

    Returns
    -------
    bool
        True if successful.
    """
    return await run_cmd(
        ["dart", "run", "build_runner", "build", "--delete-conflicting-outputs"],
        cwd=cwd,
        output=output,
        timeout=timeout,
    )

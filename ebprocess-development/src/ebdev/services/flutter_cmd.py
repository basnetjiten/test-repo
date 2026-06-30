# -*- coding: utf-8 -*-
"""Subprocess wrappers for Flutter / Dart CLI tools using non-blocking asyncio."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

_FLUTTER_ENV: dict[str, str] = {
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "FLUTTER_NO_ANALYTICS": "1",
}


def _flutter_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Construct environment dictionary for Flutter commands."""
    env = {**os.environ, **_FLUTTER_ENV}
    if extra:
        env.update(extra)
    return env


async def run_cmd(
    cmd: list[str],
    cwd: str,
    output: Optional[list[str]] = None,
    timeout: int = 120,
    env: Optional[dict[str, str]] = None,
) -> bool:
    """Run a shell command asynchronously and capture output."""
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
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Command timed out after {timeout}s")

        combined = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
        if output is not None:
            output.append(f"$ {' '.join(cmd)}\n{combined}")
        return proc.returncode == 0
    except Exception as e:
        if output is not None:
            output.append(f"$ {' '.join(cmd)}\nERROR: {e}")
        return False


async def pub_get(cwd: str, output: Optional[list[str]] = None) -> bool:
    """Run `flutter pub get`."""
    return await run_cmd(["flutter", "pub", "get"], cwd=cwd, output=output)


async def analyze(cwd: str, output: Optional[list[str]] = None) -> bool:
    """Run `flutter analyze`."""
    return await run_cmd(["flutter", "analyze"], cwd=cwd, output=output)


async def create(
    cwd: str,
    project_name: str = "generated_project",
    platforms: str = "android,ios",
    output: Optional[list[str]] = None,
) -> bool:
    """Run `flutter create` to seed a project."""
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
    """Run `dart run build_runner build --delete-conflicting-outputs`."""
    return await run_cmd(
        ["dart", "run", "build_runner", "build", "--delete-conflicting-outputs"],
        cwd=cwd,
        output=output,
        timeout=timeout,
    )


async def simplex_init(cwd: str, output: Optional[list[str]] = None) -> bool:
    """Run `simplex init --no-interactive`."""
    return await run_cmd(["simplex", "init", "--no-interactive"], cwd=cwd, output=output)

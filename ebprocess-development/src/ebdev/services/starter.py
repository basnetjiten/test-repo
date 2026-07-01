# -*- coding: utf-8 -*-
"""
starter.py
==========
Starter kit bootstrapping dispatcher for ebprocess-development.

Responsibilities
----------------
* Dispatch and coordinate starter kit scaffolding tasks (templated files or Flutter init CLI).
* Execute build runner synchronizations and dependency downloads.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ebdev.services import flutter_cmd

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------
async def _bootstrap_sync(repo_path: str) -> None:
    """Perform bootstrap synchronization commands: pub get and build_runner."""
    logger.info("Starting Bootstrap Sync Sequence...")

    logger.info("Running flutter pub get...")
    await flutter_cmd.pub_get(repo_path)

    logger.info("Running build_runner...")
    await flutter_cmd.build_runner(repo_path)

    logger.info("Bootstrap Sync Sequence completed.")


async def _copy_template(dest: Path) -> None:
    """Copy base template files to destination repository."""
    src = TEMPLATES_DIR / "flutter-base"
    if not src.exists():
        logger.warning("Flutter base template not found at %s. Scaffolding empty Flutter project instead.", src)
        await flutter_cmd.create(str(dest))
        return
    shutil.copytree(src, dest, dirs_exist_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def run_starter(starter_type: str, repo_path: str) -> None:
    """
    Bootstrap the repo using the appropriate starter mechanism.

    Parameters
    ----------
    starter_type : str
        The type of scaffolding skeleton to bootstrap ("template", "cli", "empty", etc.).
    repo_path : str
        The target directory to bootstrap.
    """
    path = Path(repo_path)

    logger.info("Bootstrapping %s in %s...", starter_type, repo_path)

    if starter_type == "template":
        await _copy_template(path)
    elif starter_type == "cli":
        if not (path / "pubspec.yaml").exists():
            logger.info("No pubspec.yaml found. Initializing Flutter project...")
            await flutter_cmd.create(repo_path)
    elif starter_type in ("empty", "flutter"):
        await flutter_cmd.create(repo_path)

    await _bootstrap_sync(repo_path)

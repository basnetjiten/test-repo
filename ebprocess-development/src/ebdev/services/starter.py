# -*- coding: utf-8 -*-
"""Starter kit bootstrapping dispatcher for ebprocess-development."""

from __future__ import annotations

import shutil
from pathlib import Path

from ebdev.core.logger import get_logger
from ebdev.services import flutter_cmd

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def run_starter(starter_type: str, repo_path: str) -> None:
    """Bootstrap the repo using the appropriate starter mechanism."""
    path = Path(repo_path)

    logger.info(f"Bootstrapping {starter_type} in {repo_path}...")

    if starter_type == "template":
        _copy_template(path)
    elif starter_type == "cli":
        if not (path / "pubspec.yaml").exists():
            logger.info("No pubspec.yaml found. Initializing Flutter project...")
            flutter_cmd.create(repo_path)
    elif starter_type in ("empty", "flutter"):
        flutter_cmd.create(repo_path)

    _bootstrap_sync(repo_path)


def _bootstrap_sync(repo_path: str) -> None:
    """Perform bootstrap synchronization commands: simplex init, pub get, and build_runner."""
    logger.info("Starting Bootstrap Sync Sequence...")

    logger.info("Running simplex init --no-interactive...")
    flutter_cmd.simplex_init(repo_path)

    logger.info("Running flutter pub get...")
    flutter_cmd.pub_get(repo_path)

    logger.info("Running build_runner...")
    flutter_cmd.build_runner(repo_path)

    logger.info("Bootstrap Sync Sequence completed.")


def _copy_template(dest: Path) -> None:
    """Copy base template files to destination repository."""
    src = TEMPLATES_DIR / "flutter-base"
    if not src.exists():
        logger.warning(f"Flutter base template not found at {src}. Scaffolding empty Flutter project instead.")
        flutter_cmd.create(str(dest))
        return
    shutil.copytree(src, dest, dirs_exist_ok=True)

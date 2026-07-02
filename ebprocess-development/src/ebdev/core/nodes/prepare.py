# -*- coding: utf-8 -*-
"""
prepare.py
==========
Prepare node - sets up repositories and branches concurrently for all platforms.

Responsibilities
----------------
* Resolve the parent workspace directory.
* Isolate platforms in subdirectories if there are multiple platforms.
* Ensure remote git repository existence.
* Clone, fetch, and checkout features branch.
* Bootstrap platform projects if not existing.
* Install platform dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.exceptions import GitServiceError
from ebdev.core.nodes.common import send_progress
from ebdev.platforms import get_platform_strategy
from ebdev.services.git import GitConflictError, GitService, RemoteRepoService

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def prepare_node(state: GraphState) -> GraphState:
    """
    Resolve workspace, checkout branches, and setup platforms concurrently.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with prepared repository paths and feature branch names.

    Raises
    ------
    GitServiceError
        If cloning, checking remote, or syncing operations fail during setup.
    """
    state.last_node = "prepare"
    await send_progress(state, "Initialising: Preparing workspace repositories concurrently...")

    ctx = state.context
    platforms = ctx.platforms

    try:
        # 1. Resolve Parent Workspace
        if ctx.project_repo:
            repo_slug = ctx.project_repo.rstrip("/").split("/")[-1].replace(".git", "")
        else:
            repo_slug = "local-project"

        workspace_id = ctx.space_name or repo_slug

        if ctx.repo_path:
            repo_path = Path(ctx.repo_path)
        else:
            repo_path = Path(config.WORKSPACE_DIR) / workspace_id
            ctx.repo_path = str(repo_path)

        repo_path.mkdir(parents=True, exist_ok=True)
        logger.info("Parent Workspace resolved to: %s", repo_path)

        # 2. Async Strategy Execution Function
        async def prepare_single_platform(platform: str) -> None:
            plat_path = ctx.platform_path(platform)
            plat_path.mkdir(parents=True, exist_ok=True)
            logger.info("[%s] Preparing platform workspace in: %s", platform, plat_path)

            git = GitService(plat_path)

            # Resolve platform-specific repo URL (e.g. suffix repo with -platform if multiple)
            plat_repo_url = ctx.project_repo
            if plat_repo_url and len(platforms) > 1:
                # Suffix base name with platform name
                clean_url = plat_repo_url.strip().replace(".git", "")
                if f"-{platform}" not in clean_url and f"_{platform}" not in clean_url:
                    if plat_repo_url.endswith(".git"):
                        plat_repo_url = plat_repo_url[:-4] + f"-{platform}.git"
                    else:
                        plat_repo_url = plat_repo_url + f"-{platform}"

            # Check and auto-create remote repository if missing
            if plat_repo_url:
                proj_key = ctx.ticket.id.split("-")[0] if ctx.ticket and "-" in ctx.ticket.id else "PROJ"
                await send_progress(state, f"Checking/creating remote repository: {plat_repo_url}...")
                repo_exists = await RemoteRepoService.ensure_repo_exists(plat_repo_url, project_key=proj_key)
                if not repo_exists:
                    logger.warning("[%s] Remote repository setup returned error for %s", platform, plat_repo_url)

                # TODO: In the future, pull these from remote Bitbucket repositories.
                # For now, resolve per-platform starter kit URL, falling back to ctx.starter_kit_url.
                resolved_starter_kit = ctx.starter_kit_urls.get(platform) or ctx.starter_kit_url

                # Clone or Fetch using the platform repo URL
                await asyncio.to_thread(git.clone_or_fetch, plat_repo_url, resolved_starter_kit)

            # Sanitized branch checkout
            sanitized_feature = _sanitize_branch_name(ctx.feature_name or ctx.ticket.title)
            branch_name = f"feature/{ctx.ticket.id}-{sanitized_feature}"

            await asyncio.to_thread(git.checkout_branch, branch_name)

            # Merge sync with main
            if git.is_git_repo():
                try:
                    await asyncio.to_thread(git.sync_with_main, ctx.branch or "main")
                except GitConflictError as e:
                    raise GitServiceError(f"Git conflict detected during sync with main on platform {platform}") from e

            # Bootstrap skeleton if needed
            strategy = get_platform_strategy(platform)
            has_pubspec = (plat_path / "pubspec.yaml").exists()
            has_python = (plat_path / "pyproject.toml").exists() or (plat_path / "requirements.txt").exists()
            has_node = (plat_path / "package.json").exists()
            is_existing = has_pubspec or has_python or has_node
            # Resolve per-platform starter type; fall back to ctx.starter_type if not mapped
            resolved_starter = ctx.starter_types.get(platform) or ctx.starter_type
            if not is_existing and resolved_starter:
                await send_progress(state, f"Bootstrapping {platform} project strategy...")
                await strategy.bootstrap(plat_path, resolved_starter)

                # Commit initial setup
                if git.is_git_repo() and not git.has_commits():
                    git.commit_all("chore: initialize project skeleton")
                    git.checkout_branch(branch_name)

            if platform == "flutter":
                await _refactor_flutter_project(plat_path, ctx.platform_dir_name("flutter"))

            # Install dependencies
            await send_progress(state, f"Resolving dependencies for platform '{platform}'...")
            await strategy.prepare(plat_path, branch_name)
            logger.info("[%s] Completed preparation successfully.", platform)

        # Execute all platform preparations concurrently
        await asyncio.gather(*[prepare_single_platform(p) for p in platforms])

    except Exception as e:
        err = f"Concurrent preparation phase failed: {str(e)}"
        logger.error(err)
        raise GitServiceError(err) from e

    # Update state context values
    sanitized_feature_slug = _sanitize_feature_name(ctx.feature_name or ctx.ticket.title)
    updated_ctx = ctx.model_copy(update={
        "repo_path": str(repo_path),
        "generated_branch": f"feature/{ctx.ticket.id}-{_sanitize_branch_name(ctx.feature_name or ctx.ticket.title)}",
        "feature_name": sanitized_feature_slug
    })

    logger.info("All platforms successfully prepared.")
    await send_progress(state, "All platform workspaces successfully prepared.")
    return state.model_copy(update={
        "last_node": "prepare",
        "context": updated_ctx
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _sanitize_feature_name(name: str) -> str:
    """
    Sanitize the feature name to a snake_case string.

    Parameters
    ----------
    name : str
        The raw feature name or ticket title.

    Returns
    -------
    str
        The sanitized snake_case feature name.
    """
    clean = re.sub(r"(?i)\b(feature|screen|page)\b", "", name).strip()
    if not clean:
        clean = name
    clean = re.sub(r'(?<!^)(?=[A-Z])', '_', clean).lower()
    clean = re.sub(r'[^a-z0-9_]', '_', clean)
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean


def _sanitize_branch_name(name: str) -> str:
    """
    Sanitize the branch name for Git compatibility.

    Parameters
    ----------
    name : str
        The raw name to sanitize.

    Returns
    -------
    str
        The branch-safe sanitized string.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", name)
    sanitized = re.sub(r"-+", "-", sanitized)
    return sanitized.strip("-")


async def _refactor_flutter_project(plat_path: Path, new_package_name: str) -> None:
    """
    Rename the Flutter project package name in pubspec.yaml and refactor all
    internal imports in the codebase (.dart files) to use the new package name.
    """
    pubspec_path = plat_path / "pubspec.yaml"
    if not pubspec_path.exists():
        return

    # 1. Read pubspec.yaml and find the old package name
    content = pubspec_path.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*([a-zA-Z0-9_\-]+)", content, re.MULTILINE)
    if not match:
        logger.warning("Could not find name field in pubspec.yaml at %s", pubspec_path)
        return
    old_package_name = match.group(1).strip()

    if old_package_name == new_package_name:
        logger.info("Flutter project package name already matches target: %s", new_package_name)
        return

    logger.info("Refactoring Flutter package from %r to %r...", old_package_name, new_package_name)

    # 2. Update the name: field inside pubspec.yaml
    new_content = content.replace(f"name: {old_package_name}", f"name: {new_package_name}", 1)
    pubspec_path.write_text(new_content, encoding="utf-8")

    # 3. Recursively refactor all imports in .dart files under lib/ and test/
    lib_dir = plat_path / "lib"
    test_dir = plat_path / "test"

    refactored_count = 0
    old_import_prefix = f"package:{old_package_name}/"
    new_import_prefix = f"package:{new_package_name}/"

    for folder in (lib_dir, test_dir):
        if not folder.exists():
            continue
        for file_path in folder.glob("**/*.dart"):
            try:
                code = file_path.read_text(encoding="utf-8")
                if old_import_prefix in code:
                    updated_code = code.replace(old_import_prefix, new_import_prefix)
                    file_path.write_text(updated_code, encoding="utf-8")
                    refactored_count += 1
            except Exception as e:
                logger.warning("Failed to refactor imports in file %s: %s", file_path, e)

    logger.info("Successfully refactored %d Dart file(s) with new import prefix.", refactored_count)


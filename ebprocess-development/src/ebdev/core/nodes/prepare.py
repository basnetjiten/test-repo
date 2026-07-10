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
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.config import config
from ebdev.core.exceptions import GitServiceError
from ebdev.core.logger import get_logger
from ebdev.core.name_utils import extract_feature_name, sanitize_branch_name
from ebdev.core.nodes.common import send_progress
from ebdev.platforms import get_platform_strategy
from ebdev.services.fs import AsyncFileSystemService
from ebdev.services.git import GitConflictError, GitService, RemoteRepoService

if TYPE_CHECKING:
    from ebdev.models.graph_state import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


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
    if ctx is None:
        err = "Pipeline invoked without a JobContext — cannot proceed."
        logger.error(err)
        return state.model_copy(update={
            "last_node": "prepare",
            "done": True,
            "failed": True,
            "status_message": err,
        })
    platforms = ctx.platforms

    try:
        # 1. Resolve Parent Workspace
        # space_name is guaranteed non-empty by JobContext validation; it drives
        # the workspace directory directly.
        workspace_id = ctx.space_name

        if ctx.repo_path:
            repo_path = Path(ctx.repo_path)
        else:
            repo_path = Path(config.WORKSPACE_DIR) / workspace_id
            ctx.repo_path = str(repo_path)

        await AsyncFileSystemService.ensure_directory(repo_path)
        logger.info("Parent Workspace resolved to: %s", repo_path)

        # 2. Async Strategy Execution Function
        async def prepare_single_platform(platform: str) -> None:
            plat_path = ctx.platform_path(platform)
            await AsyncFileSystemService.ensure_directory(plat_path)
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

                # TODO @Jiten Basnet: In the future, pull these from remote Bitbucket repositories.
                # For now, resolve per-platform starter kit URL, falling back to ctx.starter_kit_url.
                resolved_starter_kit = ctx.starter_kit_urls.get(platform) or ctx.starter_kit_url

                # Clone or Fetch using the platform repo URL
                await asyncio.to_thread(git.clone_or_fetch, plat_repo_url, resolved_starter_kit)

            # Sanitized branch checkout
            sanitized_feature = sanitize_branch_name(ctx.feature_name or ctx.ticket.title)
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

            await strategy.post_prepare(plat_path, ctx.platform_dir_name(platform))

            # Install dependencies
            await send_progress(state, f"Resolving dependencies for platform '{platform}'...")
            await strategy.prepare(plat_path, branch_name)
            logger.info("[%s] Completed preparation successfully.", platform)

        # Execute all platform preparations concurrently
        await asyncio.gather(*[prepare_single_platform(p) for p in platforms])

    except Exception as e:
        err = f"Concurrent preparation phase failed: {e!s}"
        logger.error(err)
        raise GitServiceError(err) from e

    # Update state context values
    sanitized_feature_slug = extract_feature_name(ctx.feature_name or ctx.ticket.title)
    updated_ctx = ctx.model_copy(
        update={
            "repo_path": str(repo_path),
            "generated_branch": f"feature/{ctx.ticket.id}-{sanitize_branch_name(ctx.feature_name or ctx.ticket.title)}",
            "feature_name": sanitized_feature_slug,
        }
    )

    logger.info("All platforms successfully prepared.")
    await send_progress(state, "All platform workspaces successfully prepared.")
    return state.model_copy(update={"last_node": "prepare", "context": updated_ctx})

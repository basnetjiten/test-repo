# -*- coding: utf-8 -*-
"""Prepare node - sets up repositories and branches concurrently for all platforms."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ebdev.config import config
from ebdev.models.schemas import GraphState
from ebdev.services.git import GitService, GitConflictError, RemoteRepoService
from ebdev.platforms import get_platform_strategy
from ebdev.core.exceptions import GitServiceError
from ebdev.core.nodes.common import send_progress


async def prepare_node(state: GraphState) -> GraphState:
    """Resolve workspace, checkout branches, and setup platforms concurrently using Strategy patterns."""
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
            
        workspace_id = ctx.jira_space_name or repo_slug
        
        if ctx.repo_path:
            repo_path = Path(ctx.repo_path)
        else:
            repo_path = Path(config.WORKSPACE_DIR) / workspace_id
            ctx.repo_path = str(repo_path)
            
        repo_path.mkdir(parents=True, exist_ok=True)
        print(f"[prepare] Parent Workspace: {repo_path}")

        # 2. Async Strategy Execution Function
        async def prepare_single_platform(platform: str) -> None:
            # If running multiple platforms, isolate them in subfolders
            if len(platforms) > 1:
                plat_path = repo_path / platform
            else:
                plat_path = repo_path
                
            plat_path.mkdir(parents=True, exist_ok=True)
            print(f"[prepare][{platform}] Directory: {plat_path}")
            
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
                jira_proj_key = ctx.jira_ticket.key.split("-")[0] if ctx.jira_ticket and "-" in ctx.jira_ticket.key else "PROJ"
                await send_progress(state, f"Checking/creating remote repository: {plat_repo_url}...")
                repo_exists = await RemoteRepoService.ensure_repo_exists(plat_repo_url, jira_project_key=jira_proj_key)
                if not repo_exists:
                    print(f"[prepare][{platform}] Warning: Remote repository setup returned error for {plat_repo_url}")

                # Clone or Fetch using the platform repo URL
                await asyncio.to_thread(git.clone_or_fetch, plat_repo_url, ctx.starter_kit_url)

            # Sanitized branch checkout
            sanitized_feature = _sanitize_branch_name(ctx.feature_name or ctx.jira_ticket.title)
            branch_name = f"feature/{ctx.jira_ticket.id}-{sanitized_feature}"
            
            await asyncio.to_thread(git.checkout_branch, branch_name)

            # Merge sync with main
            if git.is_git_repo():
                try:
                    await asyncio.to_thread(git.sync_with_main, ctx.branch or "main")
                except GitConflictError:
                    raise GitServiceError(f"Git conflict detected during sync with main on platform {platform}")

            # Bootstrap skeleton if needed
            strategy = get_platform_strategy(platform)
            has_pubspec = (plat_path / "pubspec.yaml").exists()
            has_python = (plat_path / "pyproject.toml").exists() or (plat_path / "requirements.txt").exists()
            is_existing = has_pubspec or has_python
            
            if not is_existing and ctx.starter_type:
                await send_progress(state, f"Bootstrapping {platform} project strategy...")
                await strategy.bootstrap(plat_path, ctx.starter_type)
                
                # Commit initial setup
                if git.is_git_repo() and git._run(["rev-parse", "HEAD"], check=False).returncode != 0:
                    git.commit_all("chore: initialize project skeleton")
                    git.checkout_branch(branch_name)

            # Install dependencies
            await send_progress(state, f"Resolving dependencies for platform '{platform}'...")
            await strategy.prepare(plat_path, branch_name)
            print(f"[prepare][{platform}] Completed preparation.")

        # Execute all platform preparations concurrently
        await asyncio.gather(*[prepare_single_platform(p) for p in platforms])

    except Exception as e:
        err = f"Concurrent preparation phase failed: {str(e)}"
        print(f"[prepare] ERROR: {err}")
        raise GitServiceError(err) from e

    # Update state context values
    sanitized_feature_slug = _sanitize_feature_name(ctx.feature_name or ctx.jira_ticket.title)
    updated_ctx = ctx.model_copy(update={
        "repo_path": str(repo_path),
        "generated_branch": f"feature/{ctx.jira_ticket.id}-{_sanitize_branch_name(ctx.feature_name or ctx.jira_ticket.title)}",
        "feature_name": sanitized_feature_slug
    })

    print("[prepare] All platforms successfully prepared.")
    await send_progress(state, "All platform workspaces successfully prepared.")
    return state.model_copy(update={
        "last_node": "prepare",
        "context": updated_ctx
    })


def _sanitize_feature_name(name: str) -> str:
    """Sanitize the feature name to a snake_case string."""
    clean = re.sub(r"(?i)\b(feature|screen|page)\b", "", name).strip()
    if not clean:
        clean = name
    clean = re.sub(r'(?<!^)(?=[A-Z])', '_', clean).lower()
    clean = re.sub(r'[^a-z0-9_]', '_', clean)
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean


def _sanitize_branch_name(name: str) -> str:
    """Sanitize the branch name for Git."""
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", name)
    sanitized = re.sub(r"-+", "-", sanitized)
    return sanitized.strip("-")

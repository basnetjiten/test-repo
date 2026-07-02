# -*- coding: utf-8 -*-
"""
publish.py
==========
Publish node - commits, pushes, and creates pull requests concurrently for all platforms.

Responsibilities
----------------
* Isolate and synchronize platform-specific git repositories.
* Commit all outstanding changes and push branch to remote.
* Call platform-specific repository APIs (Bitbucket/GitHub) to create pull requests.
* Remove temporary implementation plan files.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ebdev.config import config
from ebdev.core.nodes.common import send_progress
from ebdev.models.schemas import JobResult
from ebdev.services.git import GitConflictError, GitService

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Bitbucket REST API v2 base path.
BITBUCKET_API_BASE: str = "https://api.bitbucket.org/2.0"

#: GitHub REST API v3 base path.
GITHUB_API_BASE: str = "https://api.github.com"


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def publish_node(state: GraphState) -> GraphState:
    """
    Commit changes, push branches to remote, and open pull requests concurrently.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state containing PR URL details.
    """
    state.last_node = "publish"
    await send_progress(state, "Publishing changes and creating Pull Requests concurrently...")
    
    ctx = state.context
    platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    branch_name = ctx.generated_branch or ctx.branch
    repo_url = ctx.project_repo or config.BITBUCKET_REPO_URL

    # 1. Async Publishing Worker Function
    async def publish_single_platform(platform: str) -> str | None:
        logger.info("[%s] Starting git actions...", platform)
        plat_path = ctx.platform_path(platform)

        git = GitService(plat_path)

        if git.is_git_repo():
            try:
                await asyncio.to_thread(git.sync_with_main, ctx.branch or "main")
            except GitConflictError as e:
                await send_progress(state, f"Error: Conflict on platform {platform} during sync. Aborting.")
                raise e

            if not git.has_changes():
                logger.info("[%s] No changes detected. Skipping git push.", platform)
            else:
                commit_msg = f"feat: [{ctx.ticket.id}] {ctx.feature_name} ({platform})"
                await asyncio.to_thread(git.commit_all, commit_msg)
                
                # Resolve platform-specific repo URL
                plat_repo_url = repo_url
                if plat_repo_url and len(platforms) > 1:
                    clean_url = plat_repo_url.strip().replace(".git", "")
                    if f"-{platform}" not in clean_url and f"_{platform}" not in clean_url:
                        if plat_repo_url.endswith(".git"):
                            plat_repo_url = plat_repo_url[:-4] + f"-{platform}.git"
                        else:
                            plat_repo_url = plat_repo_url + f"-{platform}"
                await asyncio.to_thread(git.push, branch_name, plat_repo_url)

        # Resolve platform-specific repo URL
        plat_repo_url = repo_url
        if plat_repo_url and len(platforms) > 1:
            clean_url = plat_repo_url.strip().replace(".git", "")
            if f"-{platform}" not in clean_url and f"_{platform}" not in clean_url:
                if plat_repo_url.endswith(".git"):
                    plat_repo_url = plat_repo_url[:-4] + f"-{platform}.git"
                else:
                    plat_repo_url = plat_repo_url + f"-{platform}"

        # Create Pull Request
        pr_url = None
        if plat_repo_url:
            platform_type = "github" if "github.com" in plat_repo_url.lower() else "bitbucket"
            await send_progress(state, f"[{platform}] Creating Pull Request on {platform_type.capitalize()}...")
            
            if platform_type == "github":
                pr_url = await asyncio.to_thread(_create_github_pr, ctx, branch_name, plat_repo_url)
            else:
                pr_url = await asyncio.to_thread(_create_bitbucket_pr, ctx, branch_name, plat_repo_url)

        return pr_url

    try:
        # Run all platform publishers concurrently
        pr_urls = await asyncio.gather(*[publish_single_platform(p) for p in platforms])
        
        # Keep track of first valid PR url (or join them)
        valid_prs = [url for url in pr_urls if url]
        primary_pr = valid_prs[0] if valid_prs else None

        new_result = state.result
        if primary_pr:
            if state.result:
                new_result = state.result.model_copy(update={"pr_url": primary_pr})
            else:
                new_result = JobResult(
                    job_id=ctx.ticket_id,
                    space_name=ctx.space_name,
                    ticket_id=ctx.ticket_id,
                    status="success",
                    pr_url=primary_pr
                )
            await send_progress(state, f"Job Successful: PR Created at {primary_pr}")

        # Keep plan files for developer review as requested
        # for p in platforms:
        #     plan_file = Path(config.OPENCODE_PROJECT_DIR) / f"{p}_plan.md"
        #     if plan_file.exists():
        #         plan_file.unlink()
        #         logger.info("Cleanup: Removed plan %s", plan_file)

        return state.model_copy(update={
            "last_node": "publish",
            "result": new_result,
            "pull_request_url": primary_pr
        })

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("CRITICAL ERROR in publishing phase: %s", e)
        return state.model_copy(update={"last_node": "publish"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _create_bitbucket_pr(ctx, branch_name: str, repo_url: str | None = None) -> str | None:
    """
    Invoke Bitbucket API to create a PR.

    Parameters
    ----------
    ctx : JobContext
        The current execution context.
    branch_name : str
        The branch name to merge from.
    repo_url : str | None
        Optional repository URL override.

    Returns
    -------
    str | None
        The HTML URL of the created PR, or None if creation failed or skipped.
    """
    if not repo_url:
        repo_url = ctx.project_repo or config.BITBUCKET_REPO_URL
    if not repo_url:
        return None

    clean_url = repo_url.strip().rstrip("/").replace(".git", "")
    workspace = None
    slug = None
    
    if "git@bitbucket.org:" in clean_url:
        path = clean_url.split("git@bitbucket.org:")[1]
        parts = path.split("/")
        if len(parts) >= 2:
            workspace, slug = parts[-2], parts[-1]
    else:
        parts = clean_url.split("/")
        if len(parts) >= 2:
            workspace, slug = parts[-2], parts[-1]

    if not workspace or not slug:
        return None

    api_url = f"{BITBUCKET_API_BASE}/repositories/{workspace}/{slug}/pullrequests"
    
    payload = {
        "title": f"PR for {ctx.ticket.id}: {ctx.feature_name}",
        "source": { "branch": { "name": branch_name } },
        "destination": { "branch": { "name": ctx.branch or "main" } },
        "description": f"Generated by EBProcess\n\nRelates to {ctx.ticket.id}",
        "close_source_branch": False
    }
    
    user = config.BITBUCKET_USERNAME or config.GIT_USER
    token = config.BITBUCKET_APP_PASSWORD or config.GIT_TOKEN
    try:
        with httpx.Client() as client:
            response = client.post(api_url, json=payload, auth=(user, token))
            if response.status_code == 409:
                return None
            response.raise_for_status()
            return response.json().get("links", {}).get("html", {}).get("href")
    except httpx.HTTPError as e:
        logger.error("Failed to create Bitbucket PR: %s", e)
        return None


def _create_github_pr(ctx, branch_name: str, repo_url: str | None = None) -> str | None:
    """
    Invoke GitHub API to create a PR.

    Parameters
    ----------
    ctx : JobContext
        The current execution context.
    branch_name : str
        The branch name to merge from.
    repo_url : str | None
        Optional repository URL override.

    Returns
    -------
    str | None
        The HTML URL of the created PR, or None if creation failed or skipped.
    """
    if not repo_url:
        repo_url = ctx.project_repo or config.GITHUB_REPO_URL if hasattr(config, "GITHUB_REPO_URL") else ctx.project_repo
    if not repo_url:
        return None

    url_parts = repo_url.rstrip("/").replace(".git", "").split("/")
    if len(url_parts) < 2:
        return None
    
    repo = url_parts[-1]
    owner = url_parts[-2]
    
    api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
    
    payload = {
        "title": f"feat: [{ctx.ticket.id}] {ctx.feature_name}",
        "head": branch_name,
        "base": ctx.branch or "main",
        "body": f"Generated by EBProcess\n\nRelates to {ctx.ticket.id}"
    }
    
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {config.GITHUB_TOKEN or config.GIT_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    try:
        with httpx.Client() as client:
            response = client.post(api_url, json=payload, headers=headers)
            if response.status_code == 422:
                logger.info("GitHub PR already exists.")
                return None
            response.raise_for_status()
            return response.json().get("html_url")
    except httpx.HTTPError as e:
        logger.error("Failed to create GitHub PR: %s", e)
        return None

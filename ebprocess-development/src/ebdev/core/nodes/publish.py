# -*- coding: utf-8 -*-
"""Publish node - commits, pushes, and creates pull requests concurrently for all platforms."""

from __future__ import annotations

import asyncio
import httpx
from pathlib import Path

from ebdev.config import config
from ebdev.models.schemas import GraphState, JobResult
from ebdev.core.nodes.common import send_progress
from ebdev.services.git import GitService, GitConflictError


async def publish_node(state: GraphState) -> GraphState:
    """Commit changes, push branches to remote, and open pull requests concurrently for all platforms."""
    state.last_node = "publish"
    await send_progress(state, "Publishing changes and creating Pull Requests concurrently...")
    
    ctx = state.context
    platforms = ctx.platforms
    repo_path = Path(ctx.repo_path)
    branch_name = ctx.generated_branch or ctx.branch
    repo_url = ctx.project_repo or config.BITBUCKET_REPO_URL

    # 1. Async Publishing Worker Function
    async def publish_single_platform(platform: str) -> str | None:
        print(f"[publish][{platform}] Starting git actions...")
        if len(platforms) > 1:
            plat_path = repo_path / platform
        else:
            plat_path = repo_path

        git = GitService(plat_path)

        if git.is_git_repo():
            try:
                await asyncio.to_thread(git.sync_with_main, ctx.branch or "main")
            except GitConflictError:
                await send_progress(state, f"Error: Conflict on platform {platform} during sync. Aborting.")
                raise

            if not git.has_changes():
                print(f"[publish][{platform}] No changes detected. Skipping git push.")
            else:
                commit_msg = f"feat: [{ctx.ticket.id}] {ctx.feature_name} ({platform})"
                await asyncio.to_thread(git.commit_all, commit_msg)
                await asyncio.to_thread(git.push, branch_name, repo_url)

        # Create Pull Request
        pr_url = None
        if repo_url:
            platform_type = "github" if "github.com" in repo_url.lower() else "bitbucket"
            await send_progress(state, f"[{platform}] Creating Pull Request on {platform_type.capitalize()}...")
            
            if platform_type == "github":
                pr_url = await asyncio.to_thread(_create_github_pr, ctx, branch_name)
            else:
                pr_url = await asyncio.to_thread(_create_bitbucket_pr, ctx, branch_name)

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

        # Cleanup plan files concurrently
        plans_dir = Path(config.OPENCODE_PROJECT_DIR) / "plans"
        for p in platforms:
            if len(platforms) > 1:
                plan_file = plans_dir / f"{p}_plan.md"
            else:
                plan_file = plans_dir / "plan.md"
            if plan_file.exists():
                plan_file.unlink()
                print(f"[publish] Cleanup: Removed plan {plan_file.name}")

        return state.model_copy(update={
            "last_node": "publish",
            "result": new_result,
            "pull_request_url": primary_pr
        })

    except Exception as e:
        print(f"[publish] CRITICAL ERROR: {e}")
        return state.model_copy(update={"last_node": "publish"})


def _create_bitbucket_pr(ctx, branch_name: str) -> str | None:
    """Invokes Bitbucket API to create a PR."""
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

    api_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/pullrequests"
    
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
    except Exception as e:
        print(f"[publish] Failed to create Bitbucket PR: {e}")
        return None


def _create_github_pr(ctx, branch_name: str) -> str | None:
    """Invokes GitHub API to create a PR."""
    repo_url = ctx.project_repo or config.GITHUB_REPO_URL if hasattr(config, "GITHUB_REPO_URL") else ctx.project_repo
    if not repo_url:
        return None

    url_parts = repo_url.rstrip("/").replace(".git", "").split("/")
    if len(url_parts) < 2:
        return None
    
    repo = url_parts[-1]
    owner = url_parts[-2]
    
    api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    
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
                print("[publish] GitHub PR already exists.")
                return None
            response.raise_for_status()
            return response.json().get("html_url")
    except Exception as e:
        print(f"[publish] Failed to create GitHub PR: {e}")
        return None

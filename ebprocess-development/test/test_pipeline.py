# -*- coding: utf-8 -*-
"""Integration test script to dry-run the concurrent LangGraph orchestration pipeline."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src directory is in python path for local execution of scratch scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.config import config
from ebdev.models.schemas import SprintTicket, JobContext, GraphState, JobResult



async def run_test():
    print("=== STARTING CONCURRENT MULTI-PLATFORM PIPELINE DRY RUN ===")

    # Initialize a mock SprintTicket
    ticket = SprintTicket(
        id="EPIC-101",
        title="Build Offline-First User Authentication Epic",
        description="Implement local-first cache and background sync offline database storage on mobile, and REST API session validation on backend.",
        status="To Do",
        acceptance_criteria=["Offline synchronization operational", "Client-Server Contract verified"],
        figma_url=None
    )

    # Initialize JobContext running BOTH platforms (sequential stage planning will run api, then flutter)
    context = JobContext(
        job_id="job_epic_101",
        space_name="ebmobileapp",   # real project space → workspace/ebmobileapp/
        ticket_id="EPIC-101",
        ticket=ticket,
        repo_path=str(Path(config.WORKSPACE_DIR) / "ebmobileapp"),
        platforms=["flutter", "api"],
        current_agent="plan",
    )

    # Initialize initial state
    initial_state = GraphState(
        context=context,
        done=False,
        failed=False
    )

    # Mock OpenCode execution
    def mock_invoke(ctx, progress_callback=None, session_id=None):
        platform = ctx.platform
        agent = ctx.current_agent
        print(f"-> [Mock OpenCode][{platform}] Invoked for agent '{agent}'")
        
        if progress_callback:
            progress_callback(f"Running mock actions for {platform}")
            
        if "plan" in agent:
            # Write plan file into project-scoped storage: .opencode/<space_name>/<platform>_plan.md
            storage = ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR)
            plan_file = storage / f"{platform}_plan.md"
            plan_content = (
                f"# Implementation Plan - {platform.upper()}\n\n"
                "## Scope\n"
                f"Implement session and auth tokens on {platform.upper()}.\n\n"
                "## Changes\n"
                f"- src/{platform}_auth: Define handlers and methods\n"
            )
            plan_file.write_text(plan_content, encoding="utf-8")
            
            return JobResult(
                job_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary=f"Mock plan created for {platform}"
            ), f"mock-session-{platform}"
        else:
            return JobResult(
                job_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary=f"Code generated successfully for {platform}.",
                pr_url=f"https://github.com/mock/pr/{platform}"
            ), f"mock-session-{platform}"

    # Mock GitService run calls
    mock_git_run = MagicMock()
    mock_git_run.returncode = 0
    mock_git_run.stdout = "main"

    # Mock asyncio subprocess for API platform
    async def mock_communicate():
        return b"mock stdout", b"mock stderr"
        
    mock_process = MagicMock()
    mock_process.communicate = mock_communicate
    mock_process.returncode = 0

    async def mock_create_subprocess(*args, **kwargs):
        print(f"-> [Mock Subprocess] Executing: {' '.join(args)}")
        return mock_process

    import json

    # Mock OpenCode API client for orchestration node
    async def mock_create_session(self, title=None):
        return "mock-session-orchestrator"

    async def mock_send_prompt_message(self, session_id, agent, prompt, model=None):
        strategy_data = {
            "complexity": "low",
            "execution_mode": "sequential",
            "mocking_rules": "mock all things"
        }
        return {
            "parts": [{"type": "text", "text": f"```json\n{json.dumps(strategy_data)}\n```"}]
        }

    # Patch execution endpoints to bypass real subprocess calls
    patches = [
        patch("ebdev.core.nodes.plan.invoke_opencode", side_effect=mock_invoke),
        patch("ebdev.core.nodes.generate.invoke_opencode", side_effect=mock_invoke),
        patch("ebdev.services.opencode.OpenCodeAPIClient.create_session", mock_create_session),
        patch("ebdev.services.opencode.OpenCodeAPIClient.send_prompt_message", mock_send_prompt_message),
        patch("ebdev.services.git.GitService._run", return_value=mock_git_run),
        patch("ebdev.services.git.GitService.is_git_repo", return_value=True),
        patch("ebdev.services.git.GitService.checkout_branch", return_value="Switched branch"),
        patch("ebdev.services.git.GitService.sync_with_main", return_value=["Synced"]),
        patch("ebdev.services.git.GitService.has_changes", return_value=True),
        patch("ebdev.services.git.GitService.commit_all"),
        patch("ebdev.services.git.GitService.push"),
        patch("ebdev.services.flutter_cmd.pub_get", return_value=True),
        patch("ebdev.services.flutter_cmd.build_runner", return_value=True),
        patch("ebdev.services.flutter_cmd.analyze", return_value=True),
        patch("ebdev.services.flutter_cmd.create", return_value=True),
        patch("ebdev.platforms.flutter.FlutterStrategy.bootstrap"),
        patch("ebdev.platforms.api.ApiStrategy.bootstrap"),
        patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess),
        patch("ebdev.core.nodes.publish._create_github_pr", return_value="https://github.com/mock/pr/epic-101")
    ]

    for p in patches:
        p.start()

    try:
        # Import graph after applying mocks
        from ebdev.core.graph import graph

        print("Invoking Graph...")
        final_state = await graph.ainvoke(initial_state)
        
        print("\n=== CONCURRENT RUN COMPLETE ===")
        print(f"Final State done:   {final_state.get('done')}")
        print(f"Final State failed: {final_state.get('failed')}")
        print(f"Last executed node: {final_state.get('last_node')}")
        
        result = final_state.get("result")
        if result:
            print(f"Final Result Status: {result.status}")
            print(f"Final Result Summary: {result.summary}")
            print(f"PR URL: {result.pr_url}")
        else:
            print("No result found in final state!")
    finally:
        for p in reversed(patches):
            p.stop()


if __name__ == "__main__":
    asyncio.run(run_test())

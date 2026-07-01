# -*- coding: utf-8 -*-
"""Integration test script to verify path isolation:
- Platform project structures created inside workspace/
- Plans, contexts, and SPOQ configurations created inside .opencode/
- No metadata folders (.opencode) generated inside platform project folders.
"""

import asyncio
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src directory is in python path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.config import config
from ebdev.models.schemas import SprintTicket, JobContext, GraphState, JobResult


async def run_test():
    print("=== STARTING PATH ISOLATION INTEGRATION TEST ===")

    # Define clean paths based on config
    workspace_dir = Path(config.WORKSPACE_DIR)
    opencode_dir = Path(config.OPENCODE_PROJECT_DIR)

    print(f"Workspace Directory: {workspace_dir}")
    print(f"OpenCode Directory:  {opencode_dir}")

    # Clean up test directories to ensure fresh start
    for platform in ["api", "flutter"]:
        plat_workspace = workspace_dir / platform
        if plat_workspace.exists():
            shutil.rmtree(plat_workspace)
        
        plat_opencode = opencode_dir / platform
        if plat_opencode.exists():
            shutil.rmtree(plat_opencode)

    spoq_opencode = opencode_dir / "spoq"
    if spoq_opencode.exists():
        shutil.rmtree(spoq_opencode)

    # Initialize a SprintTicket
    ticket = SprintTicket(
        id="EPIC-102",
        title="Implement User Login Flow",
        description="Implement user authentication using username and password.",
        status="To Do",
        acceptance_criteria=[
            "Flutter login form includes username and password validation",
            "API backend exposes a POST /login endpoint that accepts username/password"
        ],
        figma_url=None
    )

    # Initialize JobContext running BOTH platforms in workspace directory
    context = JobContext(
        job_id="job_epic_102",
        space_name="development_space",
        ticket_id="EPIC-102",
        ticket=ticket,
        repo_path=str(workspace_dir),
        project_repo="https://bitbucket.org/basnetjiten7/test-repo.git",
        platforms=["flutter", "api"],
        current_agent="plan",
        starter_type="cli"
    )

    # Initialize initial state
    initial_state = GraphState(
        context=context,
        done=False,
        failed=False
    )

    # Mock OpenCode execution that writes plan files to the correct .opencode locations
    def mock_invoke(ctx, progress_callback=None, session_id=None):
        platform = ctx.platform
        agent = ctx.current_agent
        print(f"-> [Mock OpenCode][{platform}] Invoked for agent '{agent}'")
        
        if progress_callback:
            progress_callback(f"Running mock actions for {platform}")
            
        if "plan" in agent:
            # Create platform-specific mock plan file directly in .opencode/ (flat layout)
            plan_file = opencode_dir / f"{platform}_plan.md"
            plan_content = (
                f"# Implementation Plan - {platform.upper()}\n\n"
                "## Scope\n"
                f"Implement session and auth tokens on {platform.upper()}.\n"
            )
            plan_file.write_text(plan_content, encoding="utf-8")

            # Write platform-prefixed context.json using the real write_context method
            from ebdev.services.opencode import OpenCodeService
            OpenCodeService.write_context(ctx, opencode_dir, platform=platform)
            
            return JobResult(
                job_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary=f"Mock plan created for {platform}"
            ), f"mock-session-{platform}"
        else:
            # Mock code implementation inside the workspace (project) directory
            plat_path = workspace_dir / platform
            plat_path.mkdir(parents=True, exist_ok=True)
            
            if platform == "flutter":
                lib_dir = plat_path / "lib"
                lib_dir.mkdir(parents=True, exist_ok=True)
                (lib_dir / "main.dart").write_text("// Mock Flutter Main\n", encoding="utf-8")
            elif platform == "api":
                src_dir = plat_path / "src"
                src_dir.mkdir(parents=True, exist_ok=True)
                (src_dir / "main.py").write_text("# Mock API Main\n", encoding="utf-8")

            return JobResult(
                job_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary=f"Code generated successfully for {platform}.",
                pr_url=f"https://github.com/mock/pr/{platform}"
            ), f"mock-session-{platform}"

    # Mock git services and validation
    mock_git_run = MagicMock()
    mock_git_run.returncode = 0
    mock_git_run.stdout = "main"

    async def mock_communicate():
        return b"mock stdout", b"mock stderr"
        
    mock_process = MagicMock()
    mock_process.communicate = mock_communicate
    mock_process.returncode = 0

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    # Mock OpenCode API client for orchestration node strategy query
    async def mock_create_session(self, title=None):
        return "mock-session-orchestrator"

    async def mock_send_prompt_message(self, session_id, agent, prompt, model=None):
        import json
        strategy_data = {
            "complexity": "low",
            "execution_mode": "spoq",  # Test SPOQ mode to verify queue directories
            "mocking_rules": "mock all things"
        }
        return {
            "parts": [{"type": "text", "text": f"```json\n{json.dumps(strategy_data)}\n```"}]
        }

    # Patch executions
    with patch("ebdev.core.nodes.plan.invoke_opencode", side_effect=mock_invoke), \
         patch("ebdev.core.nodes.generate.invoke_opencode", side_effect=mock_invoke), \
         patch("ebdev.services.opencode.OpenCodeAPIClient.create_session", mock_create_session), \
         patch("ebdev.services.opencode.OpenCodeAPIClient.send_prompt_message", mock_send_prompt_message), \
         patch("ebdev.services.git.GitService._run", return_value=mock_git_run), \
         patch("ebdev.services.git.GitService.is_git_repo", return_value=True), \
         patch("ebdev.services.git.GitService.checkout_branch", return_value="Switched branch"), \
         patch("ebdev.services.git.GitService.sync_with_main", return_value=["Synced"]), \
         patch("ebdev.services.git.GitService.has_changes", return_value=True), \
         patch("ebdev.services.git.GitService.commit_all"), \
         patch("ebdev.services.git.GitService.push"), \
         patch("ebdev.services.flutter_cmd.pub_get", return_value=True), \
         patch("ebdev.services.flutter_cmd.build_runner", return_value=True), \
         patch("ebdev.services.flutter_cmd.analyze", return_value=True), \
         patch("ebdev.services.flutter_cmd.create", return_value=True), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess), \
         patch("ebdev.core.nodes.publish._create_bitbucket_pr", return_value="https://bitbucket.org/mock/pr/epic-102"), \
         patch("ebdev.core.nodes.publish._create_github_pr", return_value="https://github.com/mock/pr/epic-102"):

        from ebdev.core.graph import graph

        print("Invoking LangGraph concurrent pipeline...")
        final_state = await graph.ainvoke(initial_state)

        print("\n=== VERIFYING DIRECTORY STRUCTURE ===")

        # Assertions for central .opencode/ folder contents
        for platform in ["api", "flutter"]:
            # 1. Check context file exists in .opencode/tasks/<platform>_context.json
            context_file = opencode_dir / "tasks" / f"{platform}_context.json"
            assert context_file.exists(), f"Expected context file {context_file} to exist."
            print(f"[PASSED] Verified .opencode/tasks/{platform}_context.json exists.")

            # 2. Check workspace directories are populated and contain NO .opencode metadata folder
            plat_workspace = workspace_dir / platform
            assert plat_workspace.exists(), f"Expected project folder {plat_workspace} to exist."
            print(f"[PASSED] Verified project created at {plat_workspace}")

            opencode_in_workspace = plat_workspace / ".opencode"
            assert not opencode_in_workspace.exists(), f"Error: .opencode directory should NOT exist inside workspace project {plat_workspace}"
            print(f"[PASSED] Verified no .opencode directory inside {plat_workspace}")

        # Assertions for SPOQ Active Epics directory
        spoq_epic_dir = opencode_dir / "spoq" / "epics" / "active" / ticket.id
        assert spoq_epic_dir.exists(), f"Expected SPOQ epic folder {spoq_epic_dir} to exist."
        assert (spoq_epic_dir / "tasks").exists(), "Expected SPOQ tasks folder to exist."
        assert (spoq_epic_dir / "EPIC.md").exists(), "Expected SPOQ EPIC.md to exist."
        print(f"[PASSED] Verified SPOQ epic task queue generated at {spoq_epic_dir}")

        # Assert no spoq folders in workspace
        spoq_in_workspace = workspace_dir / "spoq"
        assert not spoq_in_workspace.exists(), "Error: spoq directory should NOT exist in the workspace root."
        print("[PASSED] Verified no spoq directory exists in workspace root.")

        print("\n=== ALL PATH ISOLATION VERIFICATIONS PASSED SUCCESSFULLY! ===")


if __name__ == "__main__":
    asyncio.run(run_test())

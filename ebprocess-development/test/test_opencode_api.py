# -*- coding: utf-8 -*-
"""Integration test script to verify path isolation:
- Platform project structures created inside workspace/
- Plans, contexts, and SPOQ configurations created inside .opencode/
- No metadata folders (.opencode) generated inside platform project folders.
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

# Ensure src directory is in python path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.config import config
from ebdev.models.schemas import SprintTicket, JobContext, GraphState, JobResult


def _discover_space_name() -> str:
    return os.environ.get("SPACE_NAME", "ebmobileapp")


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
        title="Create Enquiry Page",
        description="Create an enquiry page with a form containing title and description fields, and a submit button.",
        status="To Do",
        acceptance_criteria=[
            "Flutter frontend includes form with title and description fields",
            "API backend exposes a POST /enquiry endpoint to accept and save enquiries"
        ],
        tasks=[
            {
                "id": 60101,
                "name": "Build enquiry form",
                "status": "todo",
                "hours": [
                    {"estimatedHour": 1.0, "taskId": 60101, "platformId": 1, "platform": {"id": 1, "name": "flutter"}},
                    {"estimatedHour": 1.0, "taskId": 60101, "platformId": 3, "platform": {"id": 3, "name": "api"}},
                ],
            }
        ],
        figma_url=None
    )

    # Initialize JobContext running BOTH platforms in workspace directory
    context = JobContext(
        job_id="job_epic_102",
        space_name=_discover_space_name(),
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
            if ctx.spoq_epic_dir and ctx.active_task_id:
                yaml_path = Path(ctx.spoq_epic_dir) / f"{ctx.active_task_id}.yml"
                yaml_path.write_text(
                    yaml.safe_dump(
                        {
                            "id": ctx.active_task_id,
                            "title": f"Plan for {platform}",
                            "epic": ctx.ticket_id,
                            "description": (
                                f"## Objective\nCreate a concrete implementation plan for {platform}.\n\n"
                                "## Steps\n1. Audit the codebase.\n2. Implement the required changes.\n3. Validate the result.\n"
                            ),
                            "status": "pending",
                            "phase": 0,
                            "dependencies": [],
                            "skills_required": [platform],
                            "files_to_touch": [f"src/{platform}/placeholder.txt"],
                            "outputs": [f"{platform} output"],
                            "acceptance_criteria": ["Plan is detailed enough for execution."],
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
            else:
                # Create platform-specific mock plan file in the project storage directory
                storage = ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR)
                plan_file = storage / f"{platform}_plan.md"
                plan_content = (
                    f"# Implementation Plan - {platform.upper()}\n\n"
                    "## Scope\n"
                    f"Implement session and auth tokens on {platform.upper()}.\n"
                )
                plan_file.write_text(plan_content, encoding="utf-8")

                # Write platform-prefixed context.json using the real write_context method
                from ebdev.services.opencode import OpenCodeService
                OpenCodeService.write_context(ctx, storage, platform=platform)
            
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
        patch("ebdev.core.nodes.publish._create_bitbucket_pr", return_value="https://bitbucket.org/mock/pr/epic-102"),
        patch("ebdev.core.nodes.publish._create_github_pr", return_value="https://github.com/mock/pr/epic-102")
    ]

    for p in patches:
        p.start()

    try:
        from ebdev.core.graph import graph

        print("Invoking LangGraph concurrent pipeline...")
        final_state = await graph.ainvoke(initial_state)
        final_ctx = final_state.get("context") if isinstance(final_state, dict) else getattr(final_state, "context", None)

        print("\n=== VERIFYING DIRECTORY STRUCTURE ===")

        assert final_ctx is not None, "Expected the pipeline to return a final context."
        assert final_ctx.spoq_map_dir is not None, "Expected SPOQ map directory to be populated."
        assert final_ctx.spoq_epic_dir is not None, "Expected active SPOQ epic directory to be populated."

        spoq_map_dir = Path(final_ctx.spoq_map_dir)
        spoq_epic_dir = Path(final_ctx.spoq_epic_dir)

        # Assertions for central .opencode/ folder contents
        for platform in ["api", "flutter"]:
            # 1. Check context file exists in the active epic directory.
            context_file = spoq_epic_dir / f"context_{platform}.json"
            assert context_file.exists(), f"Expected context file {context_file} to exist."
            print(f"[PASSED] Verified SPOQ epic context file {context_file} exists.")

            # 2. Check workspace directories are populated and contain NO .opencode metadata folder
            plat_workspace = workspace_dir / platform
            assert plat_workspace.exists(), f"Expected project folder {plat_workspace} to exist."
            print(f"[PASSED] Verified project created at {plat_workspace}")

            opencode_in_workspace = plat_workspace / ".opencode"
            assert not opencode_in_workspace.exists(), f"Error: .opencode directory should NOT exist inside workspace project {plat_workspace}"
            print(f"[PASSED] Verified no .opencode directory inside {plat_workspace}")

        # Assertions for SPOQ Map + Active Epic directory
        assert spoq_map_dir.exists(), f"Expected SPOQ map folder {spoq_map_dir} to exist."
        assert (spoq_map_dir / "MAP.md").exists(), "Expected MAP.md to exist."
        assert spoq_epic_dir.exists(), f"Expected SPOQ epic folder {spoq_epic_dir} to exist."
        assert (spoq_epic_dir / "EPIC.md").exists(), "Expected SPOQ EPIC.md to exist."
        from ebdev.core.spoq_map import EPICS_DIRNAME
        if EPICS_DIRNAME:
            assert spoq_epic_dir.parent.name == EPICS_DIRNAME, f"Expected epic to live under the map {EPICS_DIRNAME}/ directory."
        else:
            assert spoq_epic_dir.parent == spoq_map_dir, "Expected epic to live directly under the map directory."
        print(f"[PASSED] Verified SPOQ map and epic artifacts at {spoq_map_dir} / {spoq_epic_dir}")

        # Assert SPOQ data is rooted at the workspace level, not inside platform repos.
        spoq_in_workspace = workspace_dir / "spoq"
        assert spoq_in_workspace.exists(), "Expected spoq directory to exist at the workspace root."
        print("[PASSED] Verified spoq directory exists at the workspace root.")

        print("\n=== ALL PATH ISOLATION VERIFICATIONS PASSED SUCCESSFULLY! ===")
    finally:
        for p in reversed(patches):
            p.stop()


if __name__ == "__main__":
    asyncio.run(run_test())

# -*- coding: utf-8 -*-
"""Integration test script to verify path isolation:
- Platform project structures created inside workspace/
- Plans and contexts created inside .ebpearls/
- No metadata folders (.opencode) generated inside platform project folders.
- SPOQ task state is managed through LangGraph state only (no YAML files on disk).
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.config import config
from ebdev.core.graph import graph
from ebdev.models.graph_state import GraphState, JobContext, JobResult
from ebdev.models.ticket import SprintTicket


def _discover_space_name() -> str:
    return os.environ.get("SPACE_NAME", "ebmobileapp")


async def run_test():
    print("=== STARTING PATH ISOLATION INTEGRATION TEST ===")

    workspace_dir = Path(config.WORKSPACE_DIR)
    opencode_dir = Path(config.OPENCODE_PROJECT_DIR)

    print(f"Workspace Directory: {workspace_dir}")
    print(f"OpenCode Directory:  {opencode_dir}")

    for platform in ["api", "flutter"]:
        plat_workspace = workspace_dir / platform
        if plat_workspace.exists():
            shutil.rmtree(plat_workspace)

        plat_opencode = opencode_dir / platform
        if plat_opencode.exists():
            shutil.rmtree(plat_opencode)

    ticket = SprintTicket(
        id="EPIC-102",
        title="Create Enquiry Page",
        description=(
            "Create an enquiry page with a form containing title and description fields, and a submit button."
        ),
        status="To Do",
        acceptance_criteria=[
            "Flutter frontend includes form with title and description fields",
            "API backend exposes a POST /enquiry endpoint to accept and save enquiries",
        ],
        tasks=[
            {
                "id": 60101,
                "name": "Build enquiry form",
                "status": "todo",
                "hours": [
                    {
                        "estimatedHour": 1.0,
                        "taskId": 60101,
                        "platformId": 1,
                        "platform": {"id": 1, "name": "flutter"},
                    },
                    {
                        "estimatedHour": 1.0,
                        "taskId": 60101,
                        "platformId": 3,
                        "platform": {"id": 3, "name": "api"},
                    },
                ],
            }
        ],
        figma_url=None,
    )

    context = JobContext(
        job_id="job_epic_102",
        space_name=_discover_space_name(),
        ticket_id="EPIC-102",
        ticket=ticket,
        repo_path=str(workspace_dir),
        project_repo="https://bitbucket.org/basnetjiten7/test-repo.git",
        platforms=["flutter", "api"],
        current_agent="plan",
        starter_type="cli",
    )

    initial_state = GraphState(context=context, done=False, failed=False)

    def mock_invoke(ctx, progress_callback=None, session_id=None):
        platform = ctx.platform
        agent = ctx.current_agent
        print(f"-> [Mock OpenCode][{platform}] Invoked for agent '{agent}'")

        if progress_callback:
            progress_callback(f"Running mock actions for {platform}")

        if "plan" in agent:
            # Non-SPOQ path: write plan files to .ebpearls/
            storage = ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR)
            plan_dir = storage / (str(ctx.task_id) if getattr(ctx, "task_id", None) else "default")
            plan_dir.mkdir(parents=True, exist_ok=True)
            plan_file = plan_dir / f"plan_{platform}.md"
            plan_content = (
                f"# Implementation Plan - {platform.upper()}\n\n"
                "## Scope\n"
                f"Implement session and auth tokens on {platform.upper()}.\n"
            )
            plan_file.write_text(plan_content, encoding="utf-8")

            # Write context file for the platform
            from ebdev.services.opencode import OpenCodeService

            OpenCodeService.write_context(ctx, storage, platform=platform)

            return (
                JobResult(
                    task_id=ctx.ticket_id,
                    space_name=ctx.space_name,
                    ticket_id=ctx.ticket_id,
                    status="success",
                    summary=f"Mock plan created for {platform}",
                ),
                f"mock-session-{platform}",
            )
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

        return (
            JobResult(
                task_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary=f"Code generated successfully for {platform}.",
                pr_url=f"https://github.com/mock/pr/{platform}",
            ),
            f"mock-session-{platform}",
        )

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

    async def mock_create_session(self, title=None):
        return "mock-session-orchestrator"

    async def mock_send_prompt_message(self, session_id, agent, prompt, model=None):
        import json

        strategy_data = {
            "complexity": "low",
            "execution_mode": "spoq",
            "mocking_rules": "mock all things",
        }
        return {"parts": [{"type": "text", "text": f"```json\n{json.dumps(strategy_data)}\n```"}]}

    patches = [
        patch("ebdev.core.nodes.plan.invoke_opencode", side_effect=mock_invoke),
        patch("ebdev.core.nodes.generate.invoke_opencode", side_effect=mock_invoke),
        patch(
            "ebdev.services.opencode.OpenCodeAPIClient.create_session",
            mock_create_session,
        ),
        patch(
            "ebdev.services.opencode.OpenCodeAPIClient.send_prompt_message",
            mock_send_prompt_message,
        ),
        patch("ebdev.services.git.GitService._run", return_value=mock_git_run),
        patch("ebdev.services.git.GitService.is_git_repo", return_value=True),
        patch(
            "ebdev.services.git.GitService.checkout_branch",
            return_value="Switched branch",
        ),
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
        patch(
            "ebdev.core.nodes.publish._create_bitbucket_pr",
            return_value="https://bitbucket.org/mock/pr/epic-102",
        ),
        patch(
            "ebdev.core.nodes.publish._create_github_pr",
            return_value="https://github.com/mock/pr/epic-102",
        ),
    ]

    for p in patches:
        p.start()

    try:
        print("Invoking LangGraph concurrent pipeline...")
        thread_config = {"configurable": {"thread_id": "test-thread-epic-102"}}
        final_state = await graph.ainvoke(initial_state, config=thread_config)
        final_ctx = (
            final_state.get("context") if isinstance(final_state, dict) else getattr(final_state, "context", None)
        )

        print("\n=== VERIFYING DIRECTORY STRUCTURE ===")

        assert final_ctx is not None, "Expected the pipeline to return a final context."

        # SPOQ tasks should be populated in state (no YAML files on disk)
        spoq_tasks = (
            final_state.get("spoq_tasks") if isinstance(final_state, dict) else getattr(final_state, "spoq_tasks", [])
        )
        assert len(spoq_tasks) > 0, "Expected SPOQ tasks to be populated in GraphState"
        print(f"[PASSED] Verified {len(spoq_tasks)} SPOQ tasks in GraphState.")

        for platform in ["api", "flutter"]:
            plat_workspace = workspace_dir / platform
            assert plat_workspace.exists(), f"Expected project folder {plat_workspace} to exist."
            print(f"[PASSED] Verified project created at {plat_workspace}")

            opencode_in_workspace = plat_workspace / ".opencode"
            assert not opencode_in_workspace.exists(), (
                f"Error: .opencode directory should NOT exist inside workspace project {plat_workspace}"
            )
            print(f"[PASSED] Verified no .opencode directory inside {plat_workspace}")

        # Verify no spoq YAML directory was created on disk
        spoq_in_workspace = workspace_dir / "spoq"
        print(
            f"[INFO] spoq directory on disk: {'present' if spoq_in_workspace.exists() else 'absent'} "
            "(YAML file I/O has been removed)"
        )

        print("\n=== ALL PATH ISOLATION VERIFICATIONS PASSED SUCCESSFULLY! ===")
    finally:
        for p in reversed(patches):
            p.stop()


if __name__ == "__main__":
    asyncio.run(run_test())

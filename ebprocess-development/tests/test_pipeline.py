# -*- coding: utf-8 -*-
"""Integration test script to dry-run the concurrent LangGraph orchestration pipeline."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src directory is in python path for local execution of scratch scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Set test environment overrides before config import to prevent writing to real .opencode
os.environ["OPENCODE_PROJECT_DIR"] = "/tmp/ebtest/opencode"
os.environ["WORKSPACE_DIR"] = "/tmp/ebtest/workspace"

from langchain_core.runnables import RunnableConfig

from ebdev.api.main import ExecutePipelineRequest
from ebdev.config import config
from ebdev.models.graph_state import GraphState, JobContext, JobResult
from ebdev.models.ticket import SprintTicket


def _discover_space_name() -> str:
    return os.environ.get("SPACE_NAME", "ebmobileapp")


async def run_test():
    print("=== STARTING CONCURRENT MULTI-PLATFORM PIPELINE DRY RUN ===")

    planning_path = Path(__file__).resolve().parent / "task_planning.json"
    with open(planning_path, "r", encoding="utf-8") as f:
        raw_planning = json.load(f)

    # Filter tasks to keep only api and flutter to avoid unpatched web/cms strategies
    if "data" in raw_planning and "getProjectPlanning" in raw_planning["data"]:
        planning = raw_planning["data"]["getProjectPlanning"]
        for epic in planning.get("epics", []):
            epic["tasks"] = [
                t for t in epic.get("tasks", [])
                if t.get("platform", {}).get("name", "").lower() in ["api", "flutter"]
            ]

    # Validate using ExecutePipelineRequest to convert raw planning format
    req = ExecutePipelineRequest.model_validate(raw_planning)

    ticket = SprintTicket(
        id=req.ticket_id,
        title=req.title,
        description=req.description,
        status="todo",
        tasks=req.tasks,
        figma_url=None,
    )

    # Initialize JobContext running BOTH platforms (sequential stage planning will run api, then flutter)
    context = JobContext(
        task_id=f"job_{req.ticket_id.lower()}",
        space_name=_discover_space_name(),
        ticket_id=req.ticket_id,
        ticket=ticket,
        repo_path=str(Path(config.WORKSPACE_DIR) / _discover_space_name()),
        platforms=req.platforms,
        current_agent="plan",
        map_id=req.map_id,
        map_epics=req.epics,
    )

    # Initialize initial state
    initial_state = GraphState(context=context, done=False, failed=False)

    # Mock OpenCode execution
    def mock_invoke(ctx, progress_callback=None, session_id=None):
        platform = ctx.platform
        agent = ctx.current_agent
        print(f"-> [Mock OpenCode][{platform}] Invoked for agent '{agent}'")

        if progress_callback:
            progress_callback(f"Running mock actions for {platform}")

        if "plan" in agent:
            # SPOQ mode: tasks enriched in GraphState only (no YAML files)
            if ctx.spoq_epic_dir and ctx.active_task_id:
                pass  # Enrichment handled by plan_node state updates
            else:
                storage = ctx.project_storage_dir(config.OPENCODE_PROJECT_DIR)
                plan_dir = storage / (str(ctx.task_id) if getattr(ctx, "task_id", None) else "default")
                plan_dir.mkdir(parents=True, exist_ok=True)
                plan_file = plan_dir / f"plan_{platform}.md"
                plan_content = (
                    f"# Implementation Plan - {platform.upper()}\n\n"
                    "## Scope\n"
                    f"Implement session and auth tokens on {platform.upper()}.\n\n"
                    "## Changes\n"
                    f"- src/{platform}_auth: Define handlers and methods\n"
                )
                plan_file.write_text(plan_content, encoding="utf-8")

            return JobResult(
                task_id=ctx.ticket_id,
                space_name=ctx.space_name,
                ticket_id=ctx.ticket_id,
                status="success",
                summary=f"Mock plan created for {platform}",
            ), f"mock-session-{platform}"
        return JobResult(
            task_id=ctx.ticket_id,
            space_name=ctx.space_name,
            ticket_id=ctx.ticket_id,
            status="success",
            summary=f"Code generated successfully for {platform}.",
            pr_url=f"https://github.com/mock/pr/{platform}",
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


    # Mock OpenCode API client for orchestration node
    async def mock_create_session(self, title=None):
        return "mock-session-orchestrator"

    async def mock_send_prompt_message(self, session_id, agent, prompt, model=None):
        strategy_data = {
            "complexity": "low",
            "execution_mode": "sequential",
            "mocking_rules": "mock all things",
            "primary_feature_name": "tips",
            "endpoints": [
                {
                    "entity": "tips",
                    "task_id": "276042",
                    "suggested_routes": ["GET /tips/daily"],
                    "graphql_mutation": "getDailyTip",
                }
            ],
            "task_contexts": {
                "276042": {
                    "task_name": "Show daily wellness tips to users",
                    "feature_name": "tips",
                    "platforms": ["api"],
                    "needs_schema": True,
                    "needs_ui": False,
                    "expected_files": {
                        "api": ["libs/data-access/src/tips/tips.schema.ts"],
                    },
                    "data_access_registrations": [],
                    "app_module_registrations": [],
                },
                "276041": {
                    "task_name": "Show daily wellness tips to users",
                    "feature_name": "tips",
                    "platforms": ["flutter"],
                    "needs_schema": False,
                    "needs_ui": True,
                    "expected_files": {
                        "flutter": ["lib/features/tips/presentation/pages/tips_page.dart"],
                    },
                    "data_access_registrations": [],
                    "app_module_registrations": [],
                }
            },
        }
        return {"parts": [{"type": "text", "text": f"```json\n{json.dumps(strategy_data)}\n```"}]}

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
        patch("ebdev.core.nodes.publish._create_github_pr", return_value="https://github.com/mock/pr/epic-101"),
        # Prevent mock session IDs from being written to or read from real Postgres/JSON DB.
        # Without this, "mock-session-flutter" etc. get persisted and break future real runs.
        patch("ebdev.core.nodes.generate.db.save_session_id", return_value=True),
        patch("ebdev.core.nodes.generate.db.get_session_id", return_value=None),
        patch("ebdev.core.nodes.generate.db.get_session_id_by_jira_id", return_value=None),
        patch("ebdev.core.nodes.plan.db.save_session_id", return_value=True),
        patch("ebdev.core.nodes.plan.db.get_session_id", return_value=None),
    ]

    for p in patches:
        p.start()

    try:
        # Import graph after applying mocks
        from ebdev.core.graph import graph  # noqa: PLC0415

        print("Invoking Graph...")
        thread_config: RunnableConfig = {"configurable": {"thread_id": f"test-thread-{req.ticket_id.lower()}"}}
        final_state = await graph.ainvoke(initial_state, config=thread_config)

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

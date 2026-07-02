# -*- coding: utf-8 -*-
"""
test_live_pipeline.py
=====================
Integration test — runs the full LangGraph orchestration pipeline live
against a local OpenCode/LLM server.

Usage:
    PYTHONPATH=src .venv/bin/python test/test_live_pipeline.py

The test simulates a real project job for the "ebmobileapp" project space,
writing all artifacts under the standard multi-project paths:
  - workspace/ebmobileapp/{api,flutter}/   ← code checkouts
  - .opencode/ebmobileapp/                 ← plan files, tasks, SPOQ
"""

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

# Ensure src is on the Python path for local execution
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.config import config
from ebdev.models.schemas import GraphState, JobContext, SprintTicket

# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------
# Real project space — drives workspace/<SPACE_NAME>/ and .opencode/<SPACE_NAME>/
SPACE_NAME = "ebmobileapp"
TICKET_ID = "EPIC-102"
JOB_ID = f"job_{TICKET_ID.lower().replace('-', '_')}"


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _inject_venv_path() -> None:
    """Add virtualenv bin to PATH so subprocess tools resolve correctly."""
    venv_bin = str((Path(__file__).resolve().parent.parent / ".venv" / "bin").resolve())
    os.environ["PATH"] = venv_bin + os.pathsep + os.environ.get("PATH", "")


def _clean_project_workspace() -> None:
    """
    Remove only this project's scoped directories so the run starts clean.

    Removes:
      - workspace/<SPACE_NAME>/api/
      - workspace/<SPACE_NAME>/flutter/
      - .opencode/<SPACE_NAME>/     (plans, tasks, SPOQ — not sessions.json / jobs.json)
    """
    base_workspace = Path(config.WORKSPACE_DIR) / SPACE_NAME
    for platform in ("api", "flutter"):
        plat_dir = base_workspace / platform
        if plat_dir.exists():
            shutil.rmtree(plat_dir)
            print(f"  Cleaned workspace: {plat_dir}")

    project_opencode = Path(config.OPENCODE_PROJECT_DIR) / SPACE_NAME
    if project_opencode.exists():
        shutil.rmtree(project_opencode)
        print(f"  Cleaned opencode storage: {project_opencode}")

    # Clean sessions.json keys matching the current Ticket ID from ALL locations
    import json
    sessions_locations = [
        Path(config.OPENCODE_PROJECT_DIR) / "sessions.json",
        Path(config.OPENCODE_PROJECT_DIR) / SPACE_NAME / "sessions.json",
    ]
    for sessions_file in sessions_locations:
        if sessions_file.exists():
            try:
                with open(sessions_file, "r") as f:
                    sessions = json.load(f)
                original_count = len(sessions)
                cleaned_sessions = {k: v for k, v in sessions.items() if TICKET_ID.lower() not in k.lower()}
                removed = original_count - len(cleaned_sessions)
                with open(sessions_file, "w") as f:
                    json.dump(cleaned_sessions, f, indent=2)
                if removed:
                    print(f"  Cleaned {removed} session(s) matching '{TICKET_ID}' from {sessions_file}")
            except Exception as e:
                print(f"  Warning: could not clean sessions.json at {sessions_file}: {e}")

    # Clean jobs.json key matching the current Ticket ID
    jobs_file = Path(config.OPENCODE_PROJECT_DIR) / "jobs.json"
    if jobs_file.exists():
        try:
            with open(jobs_file, "r") as f:
                jobs = json.load(f)
            original_count = len(jobs)
            jobs = {k: v for k, v in jobs.items() if TICKET_ID.lower() not in k.lower()}
            removed = original_count - len(jobs)
            if removed:
                with open(jobs_file, "w") as f:
                    json.dump(jobs, f, indent=2)
                print(f"  Cleaned {removed} job(s) matching '{TICKET_ID}' from jobs.json")
        except Exception as e:
            print(f"  Warning: could not clean jobs.json: {e}")

    base_workspace.mkdir(parents=True, exist_ok=True)


async def run_test() -> None:
    print("=== STARTING LIVE PIPELINE RUN ===")
    print(f"  Project space : {SPACE_NAME}")
    print(f"  Ticket        : {TICKET_ID}")
    print(f"  Workspace     : {Path(config.WORKSPACE_DIR) / SPACE_NAME}")
    print(f"  OpenCode dir  : {Path(config.OPENCODE_PROJECT_DIR) / SPACE_NAME}")

    _configure_logging()
    _inject_venv_path()
    _clean_project_workspace()

    ticket = SprintTicket(
        id=TICKET_ID,
        title="Create Enquiry Page",
        description=(
            "Create an enquiry page with a form containing title and description fields, "
            "and a submit button to send the enquiry."
        ),
        status="To Do",
        acceptance_criteria=[
            "Flutter frontend includes form with title and description fields",
            "Flutter frontend has form validation for title and description",
            "Flutter frontend has a submit button to send the enquiry",
            "API backend exposes POST /enquiry endpoint to accept and save enquiries",
        ],
        figma_url=None,
    )

    # repo_path is left empty — prepare_node resolves it as
    # workspace/<space_name>/ automatically via config.WORKSPACE_DIR
    context = JobContext(
        job_id=JOB_ID,
        space_name=SPACE_NAME,
        ticket_id=TICKET_ID,
        ticket=ticket,
        repo_path="",
        project_repo="https://bitbucket.org/basnetjiten7/test-repo.git",
        platforms=["flutter", "api"],
        current_agent="plan",
        starter_types={"api": "nestjs", "flutter": "flutter"},
    )

    initial_state = GraphState(context=context, done=False, failed=False)

    from ebdev.core.graph import graph  # import after path injection

    print("\nInvoking LangGraph live workflow pipeline...")
    try:
        final_state = await graph.ainvoke(initial_state)

        print("\n=== LIVE RUN COMPLETE ===")
        print(f"Final State done:   {final_state.get('done')}")
        print(f"Final State failed: {final_state.get('failed')}")
        print(f"Last executed node: {final_state.get('last_node')}")

        result = final_state.get("result")
        if result:
            print(f"Final Result Status:  {result.status}")
            print(f"Final Result Summary: {result.summary}")
            print(f"PR URL: {result.pr_url}")
            if result.errors:
                print(f"Errors: {result.errors}")
        else:
            print("No result found in final state.")
    except Exception as exc:
        print(f"Exception during graph execution: {exc}")
        raise


if __name__ == "__main__":
    asyncio.run(run_test())


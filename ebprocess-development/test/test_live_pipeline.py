# -*- coding: utf-8 -*-
"""Integration test script to run the concurrent LangGraph orchestration pipeline live against local Ollama/OpenCode."""

import asyncio
import shutil
import sys
from pathlib import Path

# Ensure src directory is in python path for local execution of scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.config import config
from ebdev.models.schemas import SprintTicket, JobContext, GraphState, JobResult


async def run_test():
    print("=== STARTING CONCURRENT LIVE PIPELINE RUN ===")

    # Configure verbose console logging
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout
    )

    # Add virtualenv bin to PATH so that python subprocesses (like pip and flutter) can be resolved
    import os
    venv_bin = str((Path(__file__).resolve().parent.parent / ".venv" / "bin").resolve())
    os.environ["PATH"] = venv_bin + os.pathsep + os.environ.get("PATH", "")

    # Setup clean paths using workspace configuration
    repo_path = Path(config.WORKSPACE_DIR)
    
    # Recreate clean local workspace directories for the test run
    for platform in ["api", "flutter"]:
        plat_dir = repo_path / platform
        if plat_dir.exists():
            shutil.rmtree(plat_dir)
        
        # Clean platform configurations under .opencode
        plat_opencode = Path(config.OPENCODE_PROJECT_DIR) / platform
        if plat_opencode.exists():
            shutil.rmtree(plat_opencode)

    # Clean fallback session and job databases to prevent session resumption errors
    sessions_file = Path(config.OPENCODE_PROJECT_DIR) / "sessions.json"
    if sessions_file.exists():
        sessions_file.unlink()
    jobs_file = Path(config.OPENCODE_PROJECT_DIR) / "jobs.json"
    if jobs_file.exists():
        jobs_file.unlink()
    spoq_file = Path(config.OPENCODE_PROJECT_DIR) / "spoq"
    if spoq_file.exists():
        shutil.rmtree(spoq_file)
    
    # Ensure the parent workspace path exists
    repo_path.mkdir(parents=True, exist_ok=True)

    # Initialize a SprintTicket for User Login Flow (username and password)
    ticket = SprintTicket(
        id="EPIC-102",
        title="Implement User Login Flow",
        description="Implement user authentication using username and password. On the Flutter frontend, create a login screen with form validation, and on the API backend, expose a /login endpoint that verifies the credentials and returns a session token.",
        status="To Do",
        acceptance_criteria=[
            "Flutter login form includes username and password validation",
            "API backend exposes a POST /login endpoint that accepts username/password",
            "Incorrect credentials should return an unauthorized error response"
        ],
        figma_url=None
    )

    # Initialize JobContext running BOTH platforms using Bitbucket
    context = JobContext(
        job_id="job_epic_102",
        space_name="development_space",
        ticket_id="EPIC-102",
        ticket=ticket,
        repo_path=str(repo_path),
        project_repo="https://bitbucket.org/basnetjiten7/test-repo.git",
        platforms=["flutter", "api"],
        current_agent="plan",
        starter_types={"api": "nestjs", "flutter": "flutter"}
    )

    # Initialize initial state
    initial_state = GraphState(
        context=context,
        done=False,
        failed=False
    )

    # Import graph
    from ebdev.core.graph import graph

    print("Invoking LangGraph live workflow pipeline...")
    try:
        final_state = await graph.ainvoke(initial_state)
        
        print("\n=== LIVE RUN COMPLETE ===")
        print(f"Final State done:   {final_state.get('done')}")
        print(f"Final State failed: {final_state.get('failed')}")
        print(f"Last executed node: {final_state.get('last_node')}")
        
        result = final_state.get("result")
        if result:
            print(f"Final Result Status: {result.status}")
            print(f"Final Result Summary: {result.summary}")
            print(f"PR URL: {result.pr_url}")
            if result.errors:
                print(f"Errors encountered: {result.errors}")
        else:
            print("No result found in final state!")
    except Exception as e:
        print(f"Exception raised during graph execution: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(run_test())

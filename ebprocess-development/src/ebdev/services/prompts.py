# -*- coding: utf-8 -*-
"""Agent prompt and instruction templates for ebprocess-development."""

from __future__ import annotations

from pathlib import Path

from ebdev.core.constants import Agents, Prompts, ErrorMessages
from ebdev.models.schemas import JobContext


def agent_instructions(job_context: JobContext, storage_dir: Path) -> str:
    """Return phase-specific instructions for the active agent."""
    agent = job_context.current_agent.lower()
    plan_path = storage_dir / "plans" / "plan.md"

    # Planner instructions
    if "planner" in agent or agent == "plan":
        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_PLANNING}
- GOAL: Create a comprehensive implementation plan based on requirements in context.json.
- REQUIREMENT: You MUST save the plan to {plan_path}.
- TOOLS: Use `write_file` or `run_command`.
- AVOID DUPLICATION: Always search the repository for existing directories and files before defining new ones. Re-use existing patterns instead of scaffolding new code from scratch whenever possible.

<PLAN_EXPECTATIONS>
Your plan must include:
1. Impacted files and directories.
2. Core logic changes.
3. State management or dependency updates.
4. Verification steps.
</PLAN_EXPECTATIONS>

EXAMPLE:
  mkdir -p {storage_dir}/plans && cat << 'PLANEOF' > {plan_path}
  # Implementation Plan
  ## Scope
  Brief description...
  ## Changes
  - file1.dart: Add widget X
  - file2.dart: Update logic Y
  PLANEOF
</{Prompts.INSTRUCTIONS_TAG}>"""

    # Bug fixer instructions
    elif "bug_fixer" in agent or "bug" in agent:
        ticket = job_context.ticket
        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_BUG_FIX}

<BUG_REPORT>
- Ticket:      {ticket.id}
- Summary:     {ticket.title}
- Description: {ticket.description or 'See context.json for details.'}
</BUG_REPORT>

<EXECUTION_STEPS>
1. Identify the root cause by examining the codebase and reproduction logs.
2. Apply a TARGETED fix — do not rewrite or refactor unrelated code.
3. Run `git diff` to confirm only the necessary lines changed.
</EXECUTION_STEPS>

<SUCCESS_CRITERIA>
- The fix must be verified.
- At least one code file (e.g., inside `lib/` or `src/`) MUST be changed. If no files have changed, you have FAILED.
</SUCCESS_CRITERIA>
</{Prompts.INSTRUCTIONS_TAG}>"""

    # Builder / Implementer instructions
    else:
        repo_path = Path(job_context.repo_path)
        
        mock_req = ""
        if job_context.mocking_level == "mock_repositories":
            mock_req = "- MOCKING REQUIRED: Isolate network API client calls behind clean repositories. Generate stateful Mock Repository classes with local simulated data to build decoupled, visually interactive screens.\n"
        elif job_context.mocking_level == "ui_stubs":
            mock_req = "- UI STUBS ONLY: Implement visual presentations and UI stubs without full logic/network integration.\n"

        offline_req = ""
        if job_context.offline_first:
            offline_req = "- OFFLINE-FIRST ARCHITECTURE: Enforce local storage as the single source of truth. UI must read from/write to local DB (e.g. Drift/Hive/Isar). Save mutations locally with a 'pending' status and implement queue/sync mechanisms to pull/push server updates.\n"

        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_IMPLEMENTATION}

<REQUIREMENTS>
- You MUST implement the plan found at {plan_path}
- If that file does not exist, immediately output: {{"status": "error", "reason": "{ErrorMessages.PLAN_MISSING.format(plan_path=plan_path)}"}} and stop.
- Edit or write files under the repository workspace directory to implement the plan.
{mock_req}{offline_req}</REQUIREMENTS>

<VERIFICATION_PROTOCOL>
1. Before finishing, run `git status` to verify files under the repository workspace have changed.
2. If no files have changed, you have FAILED.
3. Before declaring success, search for any "TODO" tags and ensure they are addressed.
</VERIFICATION_PROTOCOL>
</{Prompts.INSTRUCTIONS_TAG}>"""


def build_prompt(
    job_context: JobContext, storage_dir: Path, session_id: str | None = None
) -> str:
    """Return the full hydrated prompt instructions for the active agent."""
    repo_dir = Path(job_context.repo_path).absolute()

    return f"""<{Prompts.ROLE_TAG}>
Execute your role as the {job_context.current_agent} for job {job_context.ticket_id}.
</{Prompts.ROLE_TAG}>

<{Prompts.ENV_TAG}>
- STORAGE_DIR = {storage_dir}
- REPO_DIR    = {repo_dir}
</{Prompts.ENV_TAG}>

<{Prompts.PATHS_TAG}>
- Centralized Metadata: {storage_dir}
- Repository Workspace: {repo_dir} (All code changes MUST happen here)
- Requirements File:   {storage_dir}/tasks/context.json
</{Prompts.PATHS_TAG}>

<{Prompts.POLICY_TAG}>
ZERO-QUESTION POLICY:
- You are an AUTONOMOUS AGENT. You MUST NOT ask the user for clarification, permission, or technical help.
- Conversational questions (e.g., "Should I...?", "Would you like me to...?") are STRICTLY FORBIDDEN.
- If you encounter a missing file, a broken dependency, or a compilation error, you MUST attempt to RESOLVE it yourself using your tools.
- If an issue is truly unresolvable, document it in your final JSON status. Do NOT halt execution to ask a question.
</{Prompts.POLICY_TAG}>

{agent_instructions(job_context, storage_dir)}

<{Prompts.FINAL_INSTRUCTION_TAG}>
- Do NOT explain your steps in chat.
- You MUST invoke the necessary tools to perform the task.
- After all work is complete, you MUST output a final status report as a JSON object wrapped in a ```json code block.
- Wait for all tool commands to succeed before concluding your work.
</{Prompts.FINAL_INSTRUCTION_TAG}>"""

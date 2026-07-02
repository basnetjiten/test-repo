# -*- coding: utf-8 -*-
"""Agent prompt and instruction templates for ebprocess-development."""

from __future__ import annotations

import os
from pathlib import Path

from ebdev.core.constants import Prompts, ErrorMessages
from ebdev.models.schemas import JobContext


# ---------------------------------------------------------------------------
# Platform tech stack registry
# ---------------------------------------------------------------------------
PLATFORM_TECH_STACK: dict[str, str] = {
    "api": (
        "NestJS (TypeScript) monorepo. "
        "Key directory patterns:\n"
        "  - libs/data-access/src/<feature>/ → schema, repository (Must extend BaseRepo and be registered in dataAccessModels/data-access.module.ts)\n"
        "  - apps/api/src/modules/<feature>/ → controller, service, resolver, module (Registered in apps/api/src/app.module.ts)\n"
        "Use NestJS decorators (@Schema, @Prop, @InjectModel, @Injectable), DI, DTOs, and repositories extending BaseRepo."
    ),
    "flutter": (
        "Flutter/Dart mobile app. "
        "Key directory patterns:\n"
        "  - lib/features/<feature>/domain/ → entities, repositories, usecases\n"
        "  - lib/features/<feature>/data/ → models, repositories, datasources\n"
        "  - lib/features/<feature>/presentation/ → pages, cubit, widgets\n"
        "Use Clean Architecture. Repositories must return EitherResponse<T> and call processApiCall/handleAPICall.\n"
        "CRITICAL: Write ONLY valid Dart code. NEVER output Java, Kotlin, or Swift."
    ),
    "web": (
        "Next.js / React (TypeScript) web app. "
        "Key directory patterns:\n"
        "  - src/pages/ or app/ → routes\n"
        "  - src/components/ → React components\n"
        "  - src/lib/ → utilities and API clients"
    ),
    "cms": (
        "Payload CMS project. "
        "Key directory patterns:\n"
        "  - src/collections/ → collection configs\n"
        "  - src/fields/ → custom field types"
    ),
}


def to_container_path(path: Path) -> Path:
    """
    Translate host absolute paths to container-native paths.
    
    Maps host path prefixes to container mounts:
      - /Users/.../workspace/... -> /workspace/...
      - /Users/.../.opencode/...  -> /.opencode/...
    """
    path_str = str(path.absolute()).replace("\\", "/")
    if "/.opencode" in path_str:
        parts = path_str.split("/.opencode")
        return Path("/.opencode" + parts[-1])
    if "/workspace" in path_str:
        parts = path_str.split("/workspace")
        return Path("/workspace" + parts[-1])
    return path



def agent_instructions(job_context: JobContext, storage_dir: Path, platform: str = "") -> str:
    """Return phase-specific instructions for the active agent."""
    agent = job_context.current_agent.lower()
    plat = platform or job_context.platform
    storage_dir_container = to_container_path(storage_dir)
    plan_path = storage_dir_container / f"{plat}_plan.md"
    context_path = storage_dir_container / "tasks" / f"{plat}_context.json"

    # Planner instructions
    if "planner" in agent or agent == "plan":
        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_PLANNING}
- GOAL: Create a comprehensive implementation plan based on the requirements in {context_path}.
- REQUIREMENT: You MUST save the plan to {plan_path}.
- REQUIRED ACTION: Call the `write` tool with `filePath` set to '{plan_path}' and `content` set to your generated plan markdown.
- CRITICAL CONSTRAINT: Do NOT print the plan content in your chat response. Your chat response must contain ONLY the final JSON block. You MUST write the plan to the file using the `write` tool first, then output the final JSON block.
- CRITICAL REQUIREMENT: In your very first step, you MUST use the `read` tool to inspect the guidelines inside the `/.opencode/skills/` directory for your platform (e.g., read `/.opencode/skills/api-scaffolder/SKILL.md` for NestJS/api or `/.opencode/skills/feature-scaffolder/SKILL.md` for Flutter). Your plan MUST strictly adopt the exact folder paths, base classes (e.g., `BaseRepo`), and structural layers defined in those guidelines. If your plan fails to use these paths and patterns, you have FAILED.

<PLAN_EXPECTATIONS>
Your plan must include:
1. Impacted files and directories (use the PLATFORM_CONTEXT paths as a guide).
2. Core logic changes per file.
3. State management or dependency updates.
4. Verification steps.
5. Reference to which allowed skills/architecture patterns are being applied.
</PLAN_EXPECTATIONS>

EXAMPLE:
  Call the `write` tool with:
  filePath: {plan_path}
  content:
    # Implementation Plan
    ## Scope
    Brief description...
    ## Changes
    - apps/api/src/modules/user/user.service.ts: Add login method
    - lib/screens/login/login_screen.dart: Create login UI
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
        mock_req = ""
        if job_context.mocking_level == "mock_repositories":
            mock_req = "- MOCKING REQUIRED: Isolate network API client calls behind clean repositories. Generate stateful Mock Repository classes with local simulated data to build decoupled, visually interactive screens.\n"
            if job_context.spoq_epic_dir:
                repo_path = Path(job_context.repo_path).absolute()
                rel_spoq = os.path.relpath(Path(job_context.spoq_epic_dir).absolute(), repo_path)
                mock_req += f"- CONTRACT FIRST: You MUST reference the OpenAPI/data models defined in `{rel_spoq}/tasks/00-contract.yml` (if it exists) to ensure your mock endpoints exactly match the backend contract.\n"
        elif job_context.mocking_level == "ui_stubs":
            mock_req = "- UI STUBS ONLY: Implement visual presentations and UI stubs without full logic/network integration.\n"

        offline_req = ""
        if job_context.offline_first:
            offline_req = "- OFFLINE-FIRST ARCHITECTURE: Enforce local storage as the single source of truth. UI must read from/write to local DB (e.g. Drift/Hive/Isar). Save mutations locally with a 'pending' status and implement queue/sync mechanisms to pull/push server updates.\n"

        repo_path = to_container_path(Path(job_context.repo_path))

        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_IMPLEMENTATION}

<REQUIREMENTS>
- You MUST implement the plan found at {plan_path}
- If that file does not exist, immediately output: {{"status": "error", "reason": "{ErrorMessages.PLAN_MISSING.format(plan_path=plan_path)}"}} and stop.
- CRITICAL: You MUST use absolute paths starting with '{repo_path}/' for all file operations in the `write`, `read`, `edit`, `grep`, and `glob` tools. Do NOT use relative paths (e.g., use '{repo_path}/lib/main.dart' instead of 'lib/main.dart') in tool arguments, as relative paths will resolve to the incorrect default directory (/app).
- CRITICAL: You MUST anchor all commands and file operations in the repository workspace. Run `cd {repo_path}` before editing, creating files or running compilation/test tools.
- Edit or write files under the repository workspace directory to implement the plan.
- You MUST create or edit at least one source file inside the repository (e.g. inside `lib/`, `src/`, `apps/`, or `libs/`). Writing only to plan or metadata files does NOT count.
- SKILLS COMPLIANCE: Review the guidelines in the `.opencode/skills/` directory for your platform (e.g., repository interfaces, remote sources, freezed models, state management, or BaseRepo patterns). You MUST strictly follow the structural rules, file locations, coding patterns, and naming conventions defined in those skills.
{mock_req}{offline_req}</REQUIREMENTS>

<SUBAGENT_DELEGATION>
You have access to specialized subagents to delegate verification and refining tasks. You can run them using subagent task tools:
1. **Linter** (`linter`): Run this subagent to verify formatting and fix static analysis errors on your changed files.
2. **UI Refiner** (`ui_refiner`): Run this subagent to polish styling, spacing, and design system tokens on visual layouts.
</SUBAGENT_DELEGATION>

<VERIFICATION_PROTOCOL>
1. Before finishing, run `git status` or inspect the file system (e.g. via `ls` or `find`) to verify files under the repository workspace have changed.
2. If `git` is unavailable on the system, verify file existence manually. If no files have changed, you have FAILED. Create the missing files and try again.
3. Delegate verification tasks to the relevant subagent (e.g. run the `linter` subagent to analyze your changes) before completing.
4. Before declaring success, search for any "TODO" tags and ensure they are addressed.
</VERIFICATION_PROTOCOL>
</{Prompts.INSTRUCTIONS_TAG}>"""


def build_prompt(
    job_context: JobContext,
    storage_dir: Path,
    session_id: str | None = None,
    platform: str = "",
) -> str:
    """Return the full hydrated prompt instructions for the active agent."""
    repo_dir = to_container_path(Path(job_context.repo_path))
    storage_dir_container = to_container_path(storage_dir)
    plat = platform or job_context.platform
    session_line = f"\n- SESSION_ID  = {session_id}" if session_id else ""

    # Resolve platform tech stack hints
    tech_stack = PLATFORM_TECH_STACK.get(plat, f"Platform: {plat}")

    return f"""<{Prompts.ROLE_TAG}>
Execute your role as the {job_context.current_agent} for job {job_context.ticket_id}.
</{Prompts.ROLE_TAG}>

<{Prompts.ENV_TAG}>
- PLATFORM     = {plat}
- STORAGE_DIR  = {storage_dir_container}
- REPO_DIR     = {repo_dir}{session_line}
</{Prompts.ENV_TAG}>

<PLATFORM_CONTEXT>
{tech_stack}
</PLATFORM_CONTEXT>

<{Prompts.PATHS_TAG}>
- Centralized Metadata: {storage_dir_container}
- Repository Workspace: {repo_dir} (All code changes MUST happen here)
- Requirements File:    {storage_dir_container}/tasks/{plat}_context.json
- Implementation Plan:  {storage_dir_container}/{plat}_plan.md
</{Prompts.PATHS_TAG}>

<{Prompts.POLICY_TAG}>
ZERO-QUESTION POLICY:
- You are an AUTONOMOUS AGENT. You MUST NOT ask the user for clarification, permission, or technical help.
- Conversational questions (e.g., "Should I...?", "Would you like me to...?") are STRICTLY FORBIDDEN.
- NEVER halt execution to ask interactive questions about file paths, configuration, or file creation. You must make technical decisions autonomously.
- If you encounter a missing file, a broken dependency, or a compilation error, you MUST attempt to RESOLVE it yourself using your tools.
- If an issue is truly unresolvable, document it in your final JSON status. Do NOT halt execution to ask a question.
</{Prompts.POLICY_TAG}>

{agent_instructions(job_context, storage_dir, platform=plat)}

<{Prompts.FINAL_INSTRUCTION_TAG}>
- Do NOT explain your steps in chat.
- You MUST invoke the necessary tools to perform the task.
- After all work is complete, you MUST output a final status report as a JSON object wrapped in a ```json code block.
- Wait for all tool commands to succeed before concluding your work.
</{Prompts.FINAL_INSTRUCTION_TAG}>"""


def build_orchestrator_prompt(
    platforms: list[str],
    ticket_id: str,
    ticket_title: str,
    ticket_desc: str,
    ticket_ac: str,
) -> str:
    """
    Return the prompt for the Orchestrator LLM.

    Parameters
    ----------
    platforms : list[str]
        The list of target platforms.
    ticket_id : str
        The unique ID of the ticket.
    ticket_title : str
        The title of the ticket.
    ticket_desc : str
        The detailed description of the ticket.
    ticket_ac : str
        The acceptance criteria for the ticket.

    Returns
    -------
    str
        The formatted orchestrator prompt.
    """
    return f"""You are the Head Technical Architect for an autonomous multi-platform project.
Analyze this ticket and decide the best execution strategy for the platforms: {platforms}.

Ticket Details:
- ID: {ticket_id}
- Title: {ticket_title}
- Description: {ticket_desc}
- Acceptance Criteria: {ticket_ac}

Determine:
1. Complexity ("low", "medium", or "high").
2. Whether it requires an offline-first strategy (local-first storage/sync).
3. Whether it is a UI/UX-only presentation modification with no backend or database alterations.
4. Execution mode:
   - "spoq": Use Wave-Based Topological Dispatch (generate API contracts first, mock frontends in parallel, then integrate). Use this for any epic combining API and frontends.
   - "parallel": Run all platforms concurrently (for low complexity, UI-only, or independent changes).
   - "sequential": Run platforms strictly one after another.
5. Mocking level for frontends: "live" (connect directly) or "mock_repositories" (mock network/client implementations based on OpenAPI specs).
6. Reasoning: Explain the rationale.

You MUST return your decision in this exact JSON structure:
```json
{{
  "complexity": "low" | "medium" | "high",
  "offline_first": true | false,
  "ui_ux_only": true | false,
  "execution_mode": "spoq" | "parallel" | "sequential",
  "mocking_level": "live" | "mock_repositories" | "ui_stubs",
  "max_repair_iterations": 3,
  "reasoning": "rationale here"
}}
```
"""

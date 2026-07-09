# -*- coding: utf-8 -*-
"""Agent prompt and instruction templates for ebprocess-development."""

from __future__ import annotations

from pathlib import Path

from ebdev.core.constants import ErrorMessages, Prompts
from ebdev.models.graph_state import JobContext

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
        "Key directory patterns (verify exact names against existing codebase):\n"
        "  - lib/features/<feature>/domain/ → entities, repositories\n"
        "  - lib/features/<feature>/data/ → models, sources, repositories\n"
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

# ---------------------------------------------------------------------------
# Platform-specific skill registries and path examples
# ---------------------------------------------------------------------------
PLATFORM_SKILLS: dict[str, list[str]] = {
    "flutter": [
        "feature-scaffolder/SKILL.md",
        "api-integration/SKILL.md",
        "state-management/SKILL.md",
        "ui-generator/SKILL.md",
    ],
    "api": ["nestjs-graphql-resolvers/SKILL.md", "api-integration/SKILL.md"],
    "web": ["api-integration/SKILL.md"],
}

PLATFORM_PATH_EXAMPLES: dict[str, str] = {
    "flutter": "lib/main.dart",
    "api": "apps/api/src/main.ts",
    "web": "src/pages/index.tsx",
    "cms": "src/server.ts",
}


def to_container_path(path: Path) -> Path:
    """
    Translate host absolute paths to container-native paths.

    Maps host path prefixes to container mounts:
      - /Users/.../workspace/... -> /workspace/...
      - /Users/.../.opencode/...  -> /.opencode/...
    """
    path_str = str(path.absolute()).replace("\\", "/")
    if "/workspace" in path_str:
        parts = path_str.split("/workspace")
        return Path("/workspace" + parts[-1])
    if "/.opencode" in path_str:
        parts = path_str.split("/.opencode")
        return Path("/.opencode" + parts[-1])
    return path


def _legacy_task_paths(storage_dir: Path, task_id: str | int | None, platform: str) -> tuple[Path, Path]:
    """Return legacy context and plan paths for a task."""
    task_id_str = str(task_id) if task_id is not None else "default"
    if "-" in task_id_str:
        parts = task_id_str.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            phase_prefix = parts[0]
            jira_id = parts[1]
            task_id_dir = jira_id
            file_prefix = f"{phase_prefix}_"
        else:
            task_id_dir = task_id_str
            file_prefix = ""
    else:
        task_id_dir = task_id_str
        file_prefix = ""

    plans_dir = storage_dir / task_id_dir
    return (
        plans_dir / f"{file_prefix}context_{platform}.json",
        plans_dir / f"{file_prefix}plan_{platform}.md",
    )


def agent_instructions(job_context: JobContext, storage_dir: Path, platform: str = "") -> str:
    """Return phase-specific instructions for the active agent."""
    agent = job_context.current_agent.lower()
    plat = platform or job_context.platform
    storage_dir_container = to_container_path(storage_dir)
    spoq_dir: str | None = getattr(job_context, "spoq_epic_dir", None)
    context_path: Path | None = None
    plan_path: Path | None = None

    if spoq_dir is not None:
        # SPOQ mode: context and plan are inside active epic directory
        epic_dir = storage_dir_container / spoq_dir
        context_path = epic_dir / f"context_{plat}.json"
        plan_path = epic_dir / f"{job_context.active_task_id}.md"

    # Evaluator instructions
    if agent == "code_evaluator" or "evaluator" in agent:
        if spoq_dir is not None:
            assert plan_path is not None
            return f"""<{Prompts.INSTRUCTIONS_TAG}>
- GOAL: Score the completed code for task {job_context.active_task_id} against the 10 quality metrics: Syntactic Correctness (SC), Test Existence (TE), Test Pass Rate (TP), Requirements Fidelity (RF), SOLID Adherence (SA), Security (SE), Error Handling (EH), Scalability (SL), Code Clarity (CC), and Completeness (CO).
- REQUIREMENT: Read the task plan Markdown file at {plan_path}.
- CRITICAL REQUIREMENT: Evaluate the changes in the files listed in the Technical Audit table of that plan file.
- SCORING: Use the `agent-validation` skill to grade each metric (0-100). Calculate the average and identify the minimum score.
- DECISION: Pass the task ONLY if avg(M₁…M₁₀) ≥ 95 AND min(M₁…M₁₀) ≥ 80.
- JOURNAL: You MUST write your detailed evaluation journal to the `journals/` folder in the epic directory.
 - OUTPUT: Output ONLY a single JSON object containing the status, metrics, average, minimum, and a brief ≤20 line remediation instructions if failed. Do not wrap it in markdown fences.
</{Prompts.INSTRUCTIONS_TAG}>"""
        return f"""<{Prompts.INSTRUCTIONS_TAG}>
- GOAL: Run standard validation checks on platform {plat}.
</{Prompts.INSTRUCTIONS_TAG}>"""

    # Planner instructions
    if "planner" in agent or agent == "plan":
        skills_list = PLATFORM_SKILLS.get(plat, ["api-integration/SKILL.md"])
        skills_str = "\n".join(
            [f"  - For {plat.upper()}: " + ", ".join([f"read `/.opencode/skills/{s}`" for s in skills_list])]
        )
        if spoq_dir is not None:
            assert plan_path is not None
            return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_PLANNING}
- GOAL: Create a comprehensive implementation plan for task {job_context.active_task_id} based on your codebase audit.
- REQUIREMENT: You MUST save the plan using the `write` tool as a Markdown file at {plan_path}.
- CRITICAL CONSTRAINT: Do NOT print the plan content in your chat response. Your chat response must contain ONLY the final JSON object. You MUST write the plan to the file using the `write` tool first, then output the final JSON object.
- CRITICAL REQUIREMENT: In your very first step, you MUST use the `read` tool to inspect the guidelines inside the `/.opencode/skills/` directory for your platform:
{skills_str}
  Your plan MUST strictly adopt the exact folder paths, base classes, and structural layers defined in these skills. If your plan fails to use these paths and patterns, you have FAILED.
- Required Plan Shape: Adopt the exact heading styles, audit tables, and layout sections (Objective, Scope, Technical Audit table, Implementation Steps, Verification) defined in your system instruction. Write every file you will touch in the Technical Audit table.
</{Prompts.INSTRUCTIONS_TAG}>"""
        context_path, legacy_plan_path = _legacy_task_paths(storage_dir_container, job_context.task_id, plat)
        assert context_path is not None
        assert legacy_plan_path is not None
        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_PLANNING}
- GOAL: Create a comprehensive implementation plan based on the requirements in {context_path}.
- REQUIREMENT: You MUST save the plan using the `write` tool to the path specified under `Implementation Plan` ({legacy_plan_path}).
 - CRITICAL CONSTRAINT: Do NOT print the plan content in your chat response. Your chat response must contain ONLY the final JSON object. You MUST write the plan to the file using the `write` tool first, then output the final JSON object.
- CRITICAL REQUIREMENT: In your very first step, you MUST use the `read` tool to inspect the guidelines inside the `/.opencode/skills/` directory for your platform:
{skills_str}
  Your plan MUST strictly adopt the exact folder paths, base classes, and structural layers defined in these skills. If your plan fails to use these paths and patterns, you have FAILED.
- Required Plan Shape: Adopt the exact heading styles, audit tables, and layout sections defined in your system instruction's Required Plan Shape.
</{Prompts.INSTRUCTIONS_TAG}>"""

    # Bug fixer instructions
    if "bug_fixer" in agent or "bug" in agent:
        ticket = job_context.ticket
        return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_BUG_FIX}

<BUG_REPORT>
- Ticket:      {ticket.id}
- Summary:     {ticket.title}
- Description: {ticket.description or "See context.json for details."}
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
    import os

    mock_req = ""
    if job_context.mocking_level == "mock_repositories":
        mock_req = "- MOCKING REQUIRED: Isolate network API client calls behind clean repositories. Generate stateful Mock Repository classes with local simulated data to build decoupled, visually interactive screens.\n"
        if job_context.spoq_epic_dir:
            repo_path_abs = Path(job_context.repo_path).absolute()
            rel_spoq = os.path.relpath(Path(job_context.spoq_epic_dir).absolute(), repo_path_abs)
            mock_req += f"- CONTRACT FIRST: You MUST reference the OpenAPI/data models defined in `{rel_spoq}/tasks/00-contract.yml` (if it exists) to ensure your mock endpoints exactly match the backend contract.\n"
    elif job_context.mocking_level == "ui_stubs":
        mock_req = (
            "- UI STUBS ONLY: Implement visual presentations and UI stubs without full logic/network integration.\n"
        )

    offline_req = ""
    if job_context.offline_first:
        offline_req = "- OFFLINE-FIRST ARCHITECTURE: Enforce local storage as the single source of truth. UI must read from/write to local DB (e.g. Drift/Hive/Isar). Save mutations locally with a 'pending' status and implement queue/sync mechanisms to pull/push server updates.\n"

    repo_path = to_container_path(Path(job_context.repo_path))
    example_file = PLATFORM_PATH_EXAMPLES.get(plat, "src/index.ts")

    if spoq_dir is not None:
        assert context_path is not None
        assert plan_path is not None
        plan_source = f'Implement the plan found at {plan_path}. If that plan file does not exist, immediately output: {{\\"status\\": \\"error\\", \\"reason\\": \\"Plan file {plan_path} is missing.\\"}} and stop.'
    else:
        context_path, legacy_plan_path = _legacy_task_paths(storage_dir_container, job_context.task_id, plat)
        assert context_path is not None
        assert legacy_plan_path is not None
        plan_source = f'Implement the plan found at {legacy_plan_path}. If that plan file does not exist, immediately output: {{\\"status\\": \\"error\\", \\"reason\\": \\"{ErrorMessages.PLAN_MISSING.format(plan_path=legacy_plan_path)}\\"}} and stop.'

    return f"""<{Prompts.INSTRUCTIONS_TAG}>
{Prompts.PHASE_IMPLEMENTATION}

<REQUIREMENTS>
- Read the requirements from the context file found at {context_path}.
- {plan_source}
- CRITICAL: You MUST use absolute paths starting with '{repo_path}/' for all file operations in the `write`, `read`, `edit`, `grep`, and `glob` tools. Do NOT use relative paths (e.g., use '{repo_path}/{example_file}' instead of '{example_file}') in tool arguments, as relative paths will resolve to the incorrect default directory (/app).
- CRITICAL: You MUST anchor all commands and file operations in the repository workspace. Run `cd {repo_path}` before editing, creating files or running compilation/test tools.
- Edit or write files under the repository workspace directory to implement the plan.
- You MUST create or edit at least one source file inside the repository (e.g. inside `lib/`, `src/`, `apps/`, or `libs/`). Writing only to plan or metadata files does NOT count.
{mock_req}{offline_req}</REQUIREMENTS>

- Refer to your system instructions for details on subagent delegation, platform rules/skills, and verification protocols.
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
    spoq_epic_dir: str | None = job_context.spoq_epic_dir
    spoq_map_dir: str | None = getattr(job_context, "spoq_map_dir", None)

    # Resolve platform tech stack hints
    tech_stack = PLATFORM_TECH_STACK.get(plat, f"Platform: {plat}")

    # Resolve context file & plan file strings based on SPOQ execution mode
    if spoq_epic_dir is not None:
        epic_dir = storage_dir_container / spoq_epic_dir
        reqs_file_str = f"Requirements File:    {epic_dir}/context_{plat}.json"
        plan_file_str = f"SPOQ Task Plan File:  {epic_dir}/{job_context.active_task_id}.md"
        map_file_str = None
        if spoq_map_dir is not None:
            map_file_str = f"SPOQ Map File:        {to_container_path(Path(spoq_map_dir))}/MAP.md"
        map_line = f"\n- {map_file_str}" if map_file_str else ""
    else:
        # Fallback/Legacy plan naming resolution
        context_path, plan_path = _legacy_task_paths(storage_dir_container, job_context.task_id, plat)
        reqs_file_str = f"Requirements File:    {context_path}"
        plan_file_str = f"Implementation Plan:  {plan_path}"
        map_line = ""

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
- {reqs_file_str}
- {plan_file_str}{map_line}
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
- Do NOT explain your steps in chat. Your entire chat response must contain ONLY the final JSON object.
- You MUST invoke the necessary tools to perform the task.
- After all work is complete, you MUST output a final status report as a single JSON object. No introductory text, no commentary, no markdown fences.
- Wait for all tool commands to succeed before concluding your work.
</{Prompts.FINAL_INSTRUCTION_TAG}>"""


def build_orchestrator_prompt(
    platforms: list[str],
    ticket_id: str,
    ticket_title: str,
    ticket_desc: str,
    ticket_ac: str,
    task_details: list[str] | None = None,
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
    task_details : list[str] | None
        Pre-formatted task entries (e.g. "Task 41831: 'Create enquiry form' (platforms: flutter, api, 2h)").

    Returns
    -------
    str
        The formatted orchestrator prompt.
    """
    task_section = ""
    if task_details:
        task_lines = "\n".join(task_details)
        task_section = f"""
Tasks Breakdown:
{task_lines}
"""

    return f"""You are the Head Technical Architect for an autonomous multi-platform project.
Analyze this ticket and decide the best execution strategy for the platforms: {platforms}.

Ticket Details:
- ID: {ticket_id}
- Title: {ticket_title}
- Description: {ticket_desc}
- Acceptance Criteria: {ticket_ac}{task_section}

Determine:
1. Complexity ("low", "medium", or "high").
2. Whether it requires an offline-first strategy (local-first storage/sync).
3. Whether it is a UI/UX-only presentation modification with no backend or database alterations.
4. Execution mode:
   - "spoq": Use Wave-Based Topological Dispatch (generate API contracts first, mock frontends in parallel, then integrate). Use this for any epic combining API and frontends.
   - "parallel": Run all platforms concurrently (for low complexity, UI-only, or independent changes).
   - "sequential": Run platforms strictly one after another.
5. Mocking level for frontends:
   - "live": Real backend required — at least one task targets the API platform and needs real schema/endpoints.
   - "mock_repositories": No backend work needed — frontends can use mock data from API contracts.
   - "ui_stubs": Pure UI presentation — no backend, no real data models needed.
   IMPORTANT: If ANY task has 'api' in its platforms list, mocking_level MUST be "live".
6. Reasoning: Explain the rationale.

 You MUST return your decision as this exact JSON object with no markdown fences:
 {{
   "complexity": "low" | "medium" | "high",
   "offline_first": true | false,
   "ui_ux_only": true | false,
   "execution_mode": "spoq" | "parallel" | "sequential",
   "mocking_level": "live" | "mock_repositories" | "ui_stubs",
   "max_repair_iterations": 3,
   "reasoning": "rationale here"
 }}
"""

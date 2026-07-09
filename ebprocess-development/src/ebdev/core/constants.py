# -*- coding: utf-8 -*-
"""Project-wide constant definitions for ebprocess-development."""


# =========================================================================
# Agent Names (used in current_agent, agent dispatch, and routing)
# =========================================================================


class Agents:
    """Standard agent names used across the pipeline."""

    # Orchestrator
    ORCHESTRATOR = "multi_agent_orchestrator"

    # Flutter agents
    FLUTTER_PLANNER = "flutter_planner"
    FLUTTER_BUILDER = "flutter_builder"
    FLUTTER_STATE = "flutter_state"
    FLUTTER_DATA = "flutter_data"
    FLUTTER_DOMAIN = "flutter_domain"
    FLUTTER_UI = "flutter_ui"
    FLUTTER_GRAPHQL = "flutter_graphql"
    FLUTTER_LOCALIZATION = "flutter_localization"

    # API agents
    API_PLANNER = "api_planner"
    API_BUILDER = "api_builder"
    API_SCHEMA_BUILDER = "api_schema_builder"
    API_DTO_GENERATOR = "api_dto_generator"
    API_SERVICE_BUILDER = "api_service_builder"
    API_ROUTE_BUILDER = "api_route_builder"

    # Web agents
    WEB_PLANNER = "web_planner"
    WEB_BUILDER = "web_builder"

    # CMS agents
    CMS_PLANNER = "cms_planner"
    CMS_BUILDER = "cms_builder"

    # Cross-cutting agents
    UI_REFINER = "ui_refiner"
    BUG_FIXER = "bug_fixer"
    CODE_EVALUATOR = "code_evaluator"
    FIGMA_ASSETS = "@figma_assets"
    CONTRACT_AGENT = "contract_agent"

    # Phase keys (used in phase_map routing)
    PLAN = "plan"
    BUILD = "build"


# =========================================================================
# Node Names (LangGraph node identifiers)
# =========================================================================


class Nodes:
    """LangGraph node names used in graph construction and state.last_node."""

    PREPARE = "prepare"
    PREFLIGHT = "preflight_agent"
    ORCHESTRATE = "orchestrate_agent"
    PLANNER = "planner_agent"
    BUILDER = "builder_agent"
    EVALUATOR = "evaluator_agent"
    CONTRACT = "contract_agent"
    REPAIR = "repair_agent"
    PUBLISH = "publish_agent"
    FINALIZE = "finalize_agent"


# =========================================================================
# Platform Names
# =========================================================================


class Platforms:
    """Supported platform identifiers."""

    API = "api"
    FLUTTER = "flutter"
    WEB = "web"
    CMS = "cms"

    ALL = (API, FLUTTER, WEB, CMS)


# =========================================================================
# Status Strings
# =========================================================================


class Status:
    """Common status values for task lifecycle, job results, and execution."""

    # Task lifecycle (TaskStatus Literal in schemas.py)
    PLANNED = "planned"
    BUILDING = "building"
    BUILT = "built"
    EVALUATING = "evaluating"
    EVALUATE_FAILED = "evaluate_failed"
    REPAIRING = "repairing"
    PASSED = "passed"
    BLOCKED = "blocked"

    # SPOQ task statuses
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

    # JobResult / pipeline outcome
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"

    # Execution mode / mocking level
    LIVE = "live"
    MOCK_REPOSITORIES = "mock_repositories"
    UI_STUBS = "ui_stubs"

    # Strategy types
    SPOQ = "spoq"
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"

    # Complexity levels
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# =========================================================================
# Field Names (keys used in state.model_copy(update={...}) and DB dicts)
# =========================================================================


class Fields:
    """State field name constants for model_copy(update={...}) and DB storage."""

    LAST_NODE = "last_node"
    CURRENT_AGENT = "current_agent"
    ACTIVE_TASK_ID = "active_task_id"
    VALIDATION_ERRORS = "validation_errors"
    PREFLIGHT_SKIP_TO = "preflight_skip_to"
    STATUS_MESSAGE = "status_message"
    PULL_REQUEST_URL = "pull_request_url"
    GENERATED_ARTIFACTS = "generated_artifacts"
    SHARED_CONTEXT = "shared_context"
    DONE_PLATFORMS = "done_platforms"
    FAILED_PLATFORMS = "failed_platforms"
    PLATFORM_RESULTS = "platform_results"
    OPENCODE_SESSION_IDS = "opencode_session_ids"
    SPOQ_TASKS = "spoq_tasks"
    FEATURE_NAME = "feature_name"
    REPAIR_ITERATION = "repair_iteration"
    MOCKING_LEVEL = "mocking_level"
    OFFLINE_FIRST = "offline_first"
    REPO_PATH = "repo_path"


# =========================================================================
# SCM / Branch Constants
# =========================================================================


class SCM:
    """Source control manager constants."""

    MAIN_BRANCH = "main"
    FEATURE_BRANCH_PREFIX = "feature/"
    GITHUB = "github"
    BITBUCKET = "bitbucket"
    DEFAULT_SPRINT = "sprint-1"


# =========================================================================
# Error Messages
# =========================================================================


class ErrorMessages:
    """Common error message templates."""

    TIMEOUT = "OpenCode execution timed out after {timeout}s"
    EXIT_ERROR = "OpenCode exited {returncode}"
    AUTH_ERROR = "OpenCode Authentication Error: Invalid API key. Please check your .env file."
    NO_JSON_RESULT = "The agent finished without returning a valid JSON status. Incomplete run."
    PLAN_MISSING = "Plan missing at {plan_path}"


# =========================================================================
# Regex Patterns
# =========================================================================


class RegexPatterns:
    """Regex patterns for parsing and extraction."""

    OPENCODE_PREFIX = r"^\[opencode\]\s*"
    JSON_BLOCK = r"```json\s*(\{.*?\})\s*```"
    FIGMA_URL = r"https://(?:www\.)?figma\.com/(?:file|design)/([a-zA-Z0-9]+)(?:/[^?\s]+)?(?:\?[^\s]+)?"


# =========================================================================
# Prompt Tags
# =========================================================================


class Prompts:
    """Prompt-related constants (tags and headers)."""

    ROLE_TAG = "ROLE"
    ENV_TAG = "ENVIRONMENT"
    PATHS_TAG = "CRITICAL_PATHS"
    POLICY_TAG = "CONSTRAINTS_AND_POLICY"
    INSTRUCTIONS_TAG = "PHASE_INSTRUCTIONS"
    FINAL_INSTRUCTION_TAG = "FINAL_INSTRUCTION"

    PHASE_PLANNING = "PHASE: PLANNING"
    PHASE_BUG_FIX = "PHASE: BUG FIX"
    PHASE_IMPLEMENTATION = "PHASE: IMPLEMENTATION"

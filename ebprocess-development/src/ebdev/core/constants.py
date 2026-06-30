# -*- coding: utf-8 -*-
"""Project-wide constant definitions for ebprocess-development."""


class Agents:
    """Standard agent names."""
    FLUTTER_PLANNER = "flutter_planner"
    FLUTTER_BUILDER = "flutter_builder"
    UI_REFINER = "ui_refiner"
    BUG_FIXER = "bug_fixer"
    FIGMA_ASSETS = "@figma_assets"


class Commands:
    """Tool and CLI command strings."""
    OPENCODE = "opencode"
    RUN = "run"
    AGENT_FLAG = "--agent"
    DIR_FLAG = "--dir"
    FILE_FLAG = "--file"
    FORMAT_FLAG = "--format"
    MODEL_FLAG = "--model"
    PRINT_LOGS_FLAG = "--print-logs"
    JSON_FORMAT = "json"


class ErrorMessages:
    """Common error message templates."""
    TIMEOUT = "OpenCode execution timed out after {timeout}s"
    EXIT_ERROR = "OpenCode exited {returncode}"
    AUTH_ERROR = "OpenCode Authentication Error: Invalid API key. Please check your .env file."
    NO_JSON_RESULT = "The agent finished without returning a valid JSON status. Incomplete run."
    PLAN_MISSING = "Plan missing at {plan_path}"


class RegexPatterns:
    """Regex patterns for parsing and extraction."""
    OPENCODE_PREFIX = r"^\[opencode\]\s*"
    JSON_BLOCK = r"```json\s*(\{.*?\})\s*```"
    FIGMA_URL = r"https://(?:www\.)?figma\.com/(?:file|design)/([a-zA-Z0-9]+)(?:/[^?\s]+)?(?:\?[^\s]+)?"


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

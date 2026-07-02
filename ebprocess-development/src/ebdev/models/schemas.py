# -*- coding: utf-8 -*-
"""
schemas.py
==========
Pydantic data schemas and pipeline state definitions for ebprocess-development.

Responsibilities
----------------
* Define input/output schemas for pipeline jobs (JobContext, JobResult).
* Define the LangGraph pipeline execution state (GraphState).
* Provide orchestration strategy and SPOQ task schemas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------
_DEFAULT_PLATFORM: str = "flutter"


# ---------------------------------------------------------------------------
# Ticket Schema
# ---------------------------------------------------------------------------
class SprintTicket(BaseModel):
    """Represents a single sprint ticket or issue from a project management system."""

    id: str
    title: str
    description: str
    status: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    figma_url: Optional[str] = None

    @property
    def summary(self) -> str:
        """Alias for title — kept for interface compatibility."""
        return self.title


# ---------------------------------------------------------------------------
# Job Context Schema
# ---------------------------------------------------------------------------
class JobContext(BaseModel):
    """
    Pipeline execution context for a single job.

    The ``space_name`` field is the project identifier and drives all
    workspace and storage path resolution (see ``project_storage_dir``).
    """

    job_id: str
    space_name: str
    ticket_id: str
    ticket: SprintTicket
    repo_path: str

    # Platform resolution — always kept in sync by the model validator
    platform: str = _DEFAULT_PLATFORM
    platforms: List[str] = Field(default_factory=list)
    current_agent: str = "plan"

    # Repository management
    branch: str = "main"
    project_repo: Optional[str] = None
    starter_kit_url: Optional[str] = None
    # Per-platform starter kit source repositories.
    # TODO: In the future, pull these from remote Bitbucket repositories:
    # starter_kit_urls: Dict[str, str] = Field(
    #     default_factory=lambda: {
    #         "api": "https://bitbucket.org/workspace/ebthemes-api.git",
    #         "flutter": "https://bitbucket.org/workspace/flutterkit.git",
    #     }
    # )
    # For now, pulling from local Desktop/starterkit paths as requested:
    starter_kit_urls: Dict[str, str] = Field(
        default_factory=lambda: {
            "api": "/Users/ebpearls/Desktop/starterkit/ebthemes-api",
            "flutter": "/Users/ebpearls/Desktop/starterkit/flutterkit",
        }
    )
    starter_type: Optional[str] = None
    # Per-platform scaffold types. e.g. {"api": "nestjs", "flutter": "flutter"}
    starter_types: Dict[str, str] = Field(
        default_factory=lambda: {"api": "nestjs", "flutter": "flutter"}
    )
    n8n_callback_url: Optional[str] = None
    linked_ticket_ids: List[str] = Field(default_factory=list)

    # Feature and phase metadata
    feature_name: str = ""
    ticket_label: str = "feature"
    mocking_level: str = "live"
    offline_first: bool = False

    # SPOQ execution state
    spoq_epic_dir: Optional[str] = None

    # Progress tracking (excluded from serialization)
    repair_iteration: int = Field(0, exclude=True)
    generated_branch: Optional[str] = Field(None, exclude=True)
    validation_errors: List[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _populate_defaults(cls, data: dict) -> dict:
        """
        Normalise input data before field assignment.

        Responsibilities:
        - Mirror ``job_id`` ↔ ``ticket_id`` when only one is supplied.
        - Synchronise the ``platform`` string with the ``platforms`` list.
        - Derive ``feature_name`` from the ticket title when absent.
        """
        _sync_job_ids(data)
        _sync_platforms(data)
        _resolve_feature_name(data)
        return data

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def project_storage_dir(self, base_opencode_dir: str) -> Path:
        """
        Return the project-scoped storage directory inside ``.opencode/``.

        Isolates plan files, task contexts, and SPOQ data per project so
        concurrent pipeline runs across different projects never collide.

        Parameters
        ----------
        base_opencode_dir:
            Base ``.opencode/`` directory path from ``config.OPENCODE_PROJECT_DIR``.

        Returns
        -------
        Path
            Resolved path: ``<base_opencode_dir>/<space_name>/``
        """
        storage = Path(base_opencode_dir) / (self.space_name or "default")
        storage.mkdir(parents=True, exist_ok=True)
        return storage


# ---------------------------------------------------------------------------
# Private normalisation helpers (used only by model validators)
# ---------------------------------------------------------------------------
def _sync_job_ids(data: dict) -> None:
    """Mirror ``job_id`` ↔ ``ticket_id`` when only one side is provided."""
    if "job_id" not in data and "ticket_id" in data:
        data["job_id"] = data["ticket_id"]
    elif "ticket_id" not in data and "job_id" in data:
        data["ticket_id"] = data["job_id"]


def _sync_platforms(data: dict) -> None:
    """
    Ensure ``platform`` (str) and ``platforms`` (list) are always in sync.

    Resolution order:
    1. ``platform`` is set but ``platforms`` is empty  → expand to list.
    2. ``platforms`` is set but ``platform`` is empty  → derive from first item.
    3. Neither is set                                  → default to flutter.
    """
    has_platform = "platform" in data and data.get("platform")
    has_platforms = "platforms" in data and data.get("platforms")

    if has_platform and not has_platforms:
        data["platforms"] = [data["platform"]]
        return

    if has_platforms and not has_platform:
        data["platform"] = data["platforms"][0]
        return

    if not has_platform and not has_platforms:
        data["platform"] = _DEFAULT_PLATFORM
        data["platforms"] = [_DEFAULT_PLATFORM]


def _resolve_feature_name(data: dict) -> None:
    """Derive ``feature_name`` from the ticket title when it is absent."""
    if data.get("feature_name"):
        return
    ticket = data.get("ticket")
    if ticket is None:
        return
    if isinstance(ticket, dict):
        data["feature_name"] = ticket.get("title", "")
    else:
        data["feature_name"] = getattr(ticket, "title", "")


# ---------------------------------------------------------------------------
# Job Result Schema
# ---------------------------------------------------------------------------
class JobResult(BaseModel):
    """Represents the output result of a completed pipeline job."""

    job_id: str
    space_name: str
    ticket_id: str
    status: str  # "success" | "failed" | "partial"
    summary: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    pr_url: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: dict) -> dict:
        """Coerce lists of dicts/non-strings in warnings or errors into standard string list representation."""
        if isinstance(data, dict):
            # Sync key-variations for ticket_id / job_id
            if "job_id" not in data and "ticket_id" in data:
                data["job_id"] = data["ticket_id"]
            elif "ticket_id" not in data and "job_id" in data:
                data["ticket_id"] = data["job_id"]
            if "space_name" not in data:
                data["space_name"] = "default"

            for list_field in ("errors", "warnings"):
                if list_field in data and isinstance(data[list_field], list):
                    coerced = []
                    for item in data[list_field]:
                        if isinstance(item, dict):
                            # Construct friendly readable string format from dict keys
                            coerced.append(" | ".join(f"{k}: {v}" for k, v in item.items()))
                        else:
                            coerced.append(str(item))
                    data[list_field] = coerced
        return data

    @property
    def pull_request_url(self) -> Optional[str]:
        """Alias for ``pr_url`` — kept for interface compatibility."""
        return self.pr_url


# ---------------------------------------------------------------------------
# Orchestration Strategy Schema
# ---------------------------------------------------------------------------
class OrchestrationStrategy(BaseModel):
    """
    Strategy returned by the ``orchestrate_node`` to control pipeline behaviour.

    Attributes
    ----------
    complexity:
        Ticket complexity rating: ``"low"`` | ``"medium"`` | ``"high"``.
    execution_mode:
        Pipeline dispatch mode: ``"spoq"`` | ``"parallel"`` | ``"sequential"``.
    mocking_level:
        Frontend mocking approach: ``"live"`` | ``"mock_repositories"`` | ``"ui_stubs"``.
    """

    complexity: str
    offline_first: bool = False
    ui_ux_only: bool = False
    execution_mode: str
    mocking_level: str = "live"
    max_repair_iterations: int = 3
    reasoning: str = ""
    stages: Optional[List[List[str]]] = None


# ---------------------------------------------------------------------------
# SPOQ Task Schema
# ---------------------------------------------------------------------------
class SPOQTask(BaseModel):
    """Schema for a single SPOQ task YAML definition inside an epic."""

    id: str
    title: str
    epic: str
    description: str = ""
    status: str = "pending"  # pending | in_progress | completed | blocked
    phase: int = 0
    dependencies: List[str] = Field(default_factory=list)
    skills_required: List[str] = Field(default_factory=list)
    files_to_touch: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Graph State Schema
# ---------------------------------------------------------------------------
class GraphState(BaseModel):
    """
    LangGraph node state — passed between every node in the pipeline.

    Tracks execution context, strategy, per-platform build results,
    and OpenCode session IDs for resumable builds.
    """

    context: JobContext
    strategy: Optional[OrchestrationStrategy] = None
    current_stage: int = 0
    plan_path: Optional[str] = None
    done: bool = False
    retry_count: int = 0
    max_retries: int = 3
    result: Optional[JobResult] = None
    session_id: Optional[str] = None
    pull_request_url: Optional[str] = None

    # Single-node progress tracking
    validation_output: str = ""
    last_node: str = "start"
    status_message: str = ""
    opencode_session_id: Optional[str] = None
    failed: bool = False

    # Multi-platform concurrent state tracking
    validation_outputs: Dict[str, str] = Field(default_factory=dict)
    platform_results: Dict[str, JobResult] = Field(default_factory=dict)
    done_platforms: Dict[str, bool] = Field(default_factory=dict)
    failed_platforms: Dict[str, bool] = Field(default_factory=dict)
    opencode_session_ids: Dict[str, str] = Field(default_factory=dict)

    @property
    def is_spoq(self) -> bool:
        """Return True when the active execution mode is SPOQ."""
        return self.strategy is not None and self.strategy.execution_mode == "spoq"


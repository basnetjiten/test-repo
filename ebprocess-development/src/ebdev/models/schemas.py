from typing import List, Optional, Dict
from pydantic import BaseModel, Field, model_validator


class SprintTicket(BaseModel):
    id: str
    title: str
    description: str
    status: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    figma_url: Optional[str] = None

    @property
    def summary(self) -> str:
        """Alias summary to title for compatibility."""
        return self.title


class JobContext(BaseModel):
    job_id: str
    space_name: str
    ticket_id: str
    ticket: SprintTicket
    repo_path: str
    platform: str = "flutter"  # Backward compatibility fallback
    platforms: List[str] = Field(default_factory=list)  # Active platforms list
    current_agent: str = "plan"

    # Repository management properties
    branch: str = "main"
    project_repo: Optional[str] = None
    starter_kit_url: Optional[str] = None
    starter_type: Optional[str] = None
    # Per-platform scaffold types. Defaults: "api" → "nestjs", "flutter" → "flutter".
    starter_types: Dict[str, str] = Field(default_factory=lambda: {"api": "nestjs", "flutter": "flutter"})
    n8n_callback_url: Optional[str] = None
    linked_ticket_ids: List[str] = Field(default_factory=list)

    # Feature and phase metadata
    feature_name: str = ""
    ticket_label: str = "feature"
    mocking_level: str = "live"
    offline_first: bool = False

    # SPOQ State properties
    spoq_epic_dir: Optional[str] = None
    
    # Progress tracking properties
    repair_iteration: int = Field(0, exclude=True)
    generated_branch: Optional[str] = Field(None, exclude=True)
    validation_errors: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _populate_defaults(cls, data: dict) -> dict:
        """Handle fallback and default calculations."""
        # Support fallback mapping for backward compatibility when loading serialized JSON
        if "jira_ticket_id" in data and "ticket_id" not in data:
            data["ticket_id"] = data["jira_ticket_id"]
        if "jira_space_name" in data and "space_name" not in data:
            data["space_name"] = data["jira_space_name"]
        if "jira_ticket" in data and "ticket" not in data:
            data["ticket"] = data["jira_ticket"]
        if "linked_jira_ids" in data and "linked_ticket_ids" not in data:
            data["linked_ticket_ids"] = data["linked_jira_ids"]
        if "jira_label" in data and "ticket_label" not in data:
            data["ticket_label"] = data["jira_label"]

        # Standardize job_id and ticket_id
        if "job_id" not in data and "ticket_id" in data:
            data["job_id"] = data["ticket_id"]
        elif "ticket_id" not in data and "job_id" in data:
            data["ticket_id"] = data["job_id"]

        # Sync single platform string and multi platform lists
        if "platform" in data and not data.get("platforms"):
            data["platforms"] = [data["platform"]]
        elif "platforms" in data and not data.get("platform"):
            if data["platforms"]:
                data["platform"] = data["platforms"][0]
            else:
                data["platform"] = "flutter"
                data["platforms"] = ["flutter"]
        elif "platforms" not in data and "platform" not in data:
            data["platform"] = "flutter"
            data["platforms"] = ["flutter"]

        # Calculate feature name if not provided
        if not data.get("feature_name"):
            ticket = data.get("ticket")
            if ticket:
                if isinstance(ticket, dict):
                    data["feature_name"] = ticket.get("title", "")
                else:
                    data["feature_name"] = getattr(ticket, "title", "")

        return data

    def project_storage_dir(self, base_opencode_dir: str) -> "Path":
        """
        Return the project-scoped storage directory inside .opencode/.

        Isolates plan files, tasks, and SPOQ data per-project so concurrent
        pipeline runs across different projects never collide.

        Parameters
        ----------
        base_opencode_dir : str
            The base .opencode/ directory path from config.OPENCODE_PROJECT_DIR.

        Returns
        -------
        Path
            Resolved path: ``<base_opencode_dir>/<space_name>/``
        """
        from pathlib import Path  # local import avoids top-level circular dep
        project_key = self.space_name or "default"
        storage = Path(base_opencode_dir) / project_key
        storage.mkdir(parents=True, exist_ok=True)
        return storage


class JobResult(BaseModel):
    job_id: str
    space_name: str

    ticket_id: str
    status: str  # "success" | "failed" | "partial"
    summary: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    pr_url: Optional[str] = None  # Pull request link

    @model_validator(mode="before")
    @classmethod
    def _populate_defaults(cls, data: dict) -> dict:
        """Handle fallback and default calculations."""
        if "jira_space_name" in data and "space_name" not in data:
            data["space_name"] = data["jira_space_name"]
        if "jira_id" in data and "ticket_id" not in data:
            data["ticket_id"] = data["jira_id"]
        return data

    @property
    def pull_request_url(self) -> Optional[str]:
        """Alias pull_request_url to pr_url for compatibility."""
        return self.pr_url


class OrchestrationStrategy(BaseModel):
    complexity: str  # "low" | "medium" | "high"
    offline_first: bool = False
    ui_ux_only: bool = False
    execution_mode: str  # "spoq" | "parallel" | "sequential"
    mocking_level: str = "live"  # "live" | "mock_repositories" | "ui_stubs"
    max_repair_iterations: int = 3
    reasoning: str = ""
    stages: Optional[List[List[str]]] = None


class SPOQTask(BaseModel):
    """Schema for a SPOQ task YAML definition."""
    id: str
    title: str
    epic: str
    status: str = "pending"  # pending | in_progress | completed | blocked
    phase: int = 0
    dependencies: List[str] = Field(default_factory=list)
    skills_required: List[str] = Field(default_factory=list)
    files_to_touch: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    description: str = ""


class GraphState(BaseModel):
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

    # Progressive nodes state tracking
    validation_output: str = ""
    last_node: str = "start"
    status_message: str = ""
    opencode_session_id: Optional[str] = None
    failed: bool = False

    # Concurrency and multi-platform progressive state tracking
    validation_outputs: Dict[str, str] = Field(default_factory=dict)
    platform_results: Dict[str, JobResult] = Field(default_factory=dict)
    done_platforms: Dict[str, bool] = Field(default_factory=dict)
    failed_platforms: Dict[str, bool] = Field(default_factory=dict)
    opencode_session_ids: Dict[str, str] = Field(default_factory=dict)

    @property
    def is_spoq(self) -> bool:
        """Helper to quickly check if current execution mode is SPOQ."""
        return self.strategy is not None and self.strategy.execution_mode == "spoq"

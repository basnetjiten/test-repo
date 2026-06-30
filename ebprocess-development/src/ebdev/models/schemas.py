from typing import List, Optional, Dict
from pydantic import BaseModel, Field, model_validator


class JiraTicket(BaseModel):
    id: str
    key: str
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
    jira_space_name: str
    jira_ticket_id: str
    jira_ticket: JiraTicket
    repo_path: str
    platform: str = "flutter"  # Backward compatibility fallback
    platforms: List[str] = Field(default_factory=list)  # Active platforms list
    current_agent: str = "plan"

    # Repository management properties
    branch: str = "main"
    project_repo: Optional[str] = None
    starter_kit_url: Optional[str] = None
    starter_type: Optional[str] = None
    n8n_callback_url: Optional[str] = None
    linked_jira_ids: List[str] = Field(default_factory=list)

    # Feature and phase metadata
    feature_name: str = ""
    jira_label: str = "feature"

    # Progress tracking properties
    repair_iteration: int = Field(0, exclude=True)
    generated_branch: Optional[str] = Field(None, exclude=True)
    validation_errors: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _populate_defaults(cls, data: dict) -> dict:
        """Handle fallback and default calculations."""
        # Standardize job_id and jira_ticket_id
        if "job_id" not in data and "jira_ticket_id" in data:
            data["job_id"] = data["jira_ticket_id"]
        elif "jira_ticket_id" not in data and "job_id" in data:
            data["jira_ticket_id"] = data["job_id"]

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
            jira_ticket = data.get("jira_ticket")
            if jira_ticket:
                if isinstance(jira_ticket, dict):
                    data["feature_name"] = jira_ticket.get("title", "")
                else:
                    data["feature_name"] = getattr(jira_ticket, "title", "")

        return data


class JobResult(BaseModel):
    job_id: str
    jira_space_name: str
    jira_id: str
    status: str  # "success" | "failed" | "partial"
    summary: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    pr_url: Optional[str] = None  # Pull request link

    @property
    def pull_request_url(self) -> Optional[str]:
        """Alias pull_request_url to pr_url for compatibility."""
        return self.pr_url


class GraphState(BaseModel):
    context: JobContext
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

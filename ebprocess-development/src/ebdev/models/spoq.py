# -*- coding: utf-8 -*-
"""
spoq.py
=======
Pydantic data schemas for SPOQ tasks, epics, and maps.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from ebdev.models.ticket import EpicTask


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


class SPOQMapEpic(BaseModel):
    """Program-level epic entry used by SPOQ maps."""

    id: str
    title: str
    description: str = ""
    status: str = "planned"
    sprint: str = "sprint-1"
    depends_on: List[str] = Field(default_factory=list)
    platforms: List[str] = Field(default_factory=list)
    tasks: List[EpicTask] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    path: Optional[str] = None
    estimated_hours: float = 0.0

    @model_validator(mode="after")
    def _normalize(self) -> "SPOQMapEpic":
        """Derive stable defaults from the task payload when omitted."""
        if not self.platforms:
            platforms: list[str] = []
            for task in self.tasks:
                platforms.extend(task.active_platforms)
            self.platforms = list(dict.fromkeys(platforms))

        if not self.estimated_hours and self.tasks:
            total_hours = 0.0
            for task in self.tasks:
                total_hours += sum(hour.estimatedHour for hour in task.hours)
            self.estimated_hours = round(total_hours, 1)

        return self


class SPOQMap(BaseModel):
    """Program-level SPOQ map that coordinates multiple epics."""

    id: str
    title: str
    vision: str
    status: str = "planned"
    epics: List[SPOQMapEpic] = Field(default_factory=list)
    epic_dependencies: Dict[str, List[str]] = Field(default_factory=dict)
    dispatch_strategy: str = "wave-based"
    success_criteria: List[str] = Field(default_factory=list)
    estimated_effort: Dict[str, float] = Field(default_factory=dict)
    risk_assessment: List[str] = Field(default_factory=list)
    wave_assignments: List[List[str]] = Field(default_factory=list)
    map_dir: Optional[str] = None

    @model_validator(mode="after")
    def _normalize(self) -> "SPOQMap":
        """Keep epic dependency data aligned with epic metadata."""
        if not self.epic_dependencies and self.epics:
            self.epic_dependencies = {epic.id: list(epic.depends_on) for epic in self.epics}

        if not self.estimated_effort and self.epics:
            self.estimated_effort = {epic.id: epic.estimated_hours for epic in self.epics}

        return self

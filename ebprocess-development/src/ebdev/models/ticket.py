# -*- coding: utf-8 -*-
"""
ticket.py
=========
Pydantic data schemas for sprint tickets and epics.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class EpicTaskPlatform(BaseModel):
    """Represents a development platform for a task, such as Flutter, API, or Web."""

    id: int
    name: str


class EpicTaskHour(BaseModel):
    """Represents estimated hour details for a task on a specific platform."""

    estimatedHour: float  # noqa: N815 — external API field
    taskId: int  # noqa: N815 — external API field
    platformId: int  # noqa: N815 — external API field
    platform: EpicTaskPlatform


class EpicTask(BaseModel):
    """Represents a specific task associated with an epic, containing estimated hours by platform."""

    id: int
    name: str
    status: str
    hours: List[EpicTaskHour] = Field(default_factory=list)

    @property
    def active_platforms(self) -> List[str]:
        """Return lowercase platform names that have > 0 estimated hours."""
        active = []
        for h in self.hours:
            if h.estimatedHour > 0:
                plat_name = h.platform.name.lower()
                # Map standard names to internal keys if necessary
                if plat_name == "web app":
                    active.append("web")
                else:
                    active.append(plat_name)
        return list(dict.fromkeys(active))


class SprintTicket(BaseModel):
    """Represents a single sprint ticket or epic from a project management system."""

    id: str
    title: str
    description: str
    status: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    figma_url: Optional[str] = None
    tasks: List[EpicTask] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        """Alias for title — kept for interface compatibility."""
        return self.title

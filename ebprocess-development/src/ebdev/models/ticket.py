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


class EpicTask(BaseModel):
    """Represents a specific task associated with an epic."""

    id: int
    name: str
    status: str
    platform: Optional[EpicTaskPlatform] = None
    platformId: Optional[int] = None

    @property
    def active_platforms(self) -> List[str]:
        """Return lowercase platform names that have > 0 estimated hours."""
        if not self.platform:
            return []
        plat_name = self.platform.name.lower()
        if plat_name == "web app":
            return ["web"]
        return [plat_name]


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

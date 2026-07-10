# -*- coding: utf-8 -*-
"""
epic_state.py
=============
Async service for reading and writing the `.ebpearls/Epic-{id}/state.json`
artifact registry.

Responsibilities
----------------
* Load an `EpicStateSnapshot` for a given epic directory, returning a fresh
  empty snapshot when the file does not yet exist.
* Persist an `EpicStateSnapshot` atomically via a write-then-rename strategy
  so concurrent async tasks never observe a partially written file.
* Provide a high-level `update_task_state` helper that merges a single
  `TaskArtifactState` into the snapshot without requiring callers to manage
  file I/O directly.

Architecture Notes
------------------
* This service is the **only** layer that performs I/O on ``state.json``.
  Node modules in ``core/nodes/`` call it via the public helpers; they never
  open the file themselves.
* The LangGraph checkpoint (``AsyncPostgresSaver``) remains the **authoritative**
  source for pipeline position and full ``GraphState``.  ``state.json`` is a
  human-readable projection consumed by OpenCode agents, which cannot query
  Postgres directly.
* Write operations are wrapped in ``asyncio.to_thread`` so the async event
  loop is never blocked by filesystem calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ebdev.core.exceptions import EpicStateError
from ebdev.core.logger import get_logger
from ebdev.models.task import EpicStateSnapshot, TaskArtifactState
from ebdev.services.fs import AsyncFileSystemService

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
_STATE_FILENAME: str = "state.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class EpicStateService:
    """
    Async façade for reading and writing the `.ebpearls/Epic-{id}/state.json`
    artifact registry.

    All public methods are coroutines; blocking filesystem operations are
    dispatched to the default ``ThreadPoolExecutor`` via ``asyncio.to_thread``
    to keep the event loop responsive during concurrent pipeline runs.

    Usage
    -----
    Instantiate once per epic directory and reuse across node calls::

        svc = EpicStateService(epic_dir)
        snapshot = await svc.load()
        updated = await svc.update_task_state(
            snapshot,
            TaskArtifactState(task_id="contract-41831", platform="api", status="building"),
        )
    """

    def __init__(self, epic_dir: Path) -> None:
        """
        Parameters
        ----------
        epic_dir:
            Absolute path to the epic directory, e.g.
            ``workspace/AgentSwipe/.ebpearls/Epic-44445``.
        """
        self._epic_dir: Path = epic_dir
        self._state_path: Path = epic_dir / _STATE_FILENAME

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def load(self) -> Optional[EpicStateSnapshot]:
        """
        Load the epic's artifact registry from disk.

        Returns
        -------
        EpicStateSnapshot | None
            The persisted snapshot, or ``None`` when ``state.json`` does not
            yet exist (first run).

        Raises
        ------
        EpicStateError
            When the file exists but is corrupt or fails schema validation.
        """
        try:
            if not await AsyncFileSystemService.exists(self._state_path):
                return None
            data = await AsyncFileSystemService.read_json(self._state_path)
            snapshot = EpicStateSnapshot.model_validate(data)
        except Exception as exc:
            raise EpicStateError(f"Failed to parse state.json at {self._state_path}: {exc}") from exc

        logger.debug(
            "Loaded epic state for %s (%d tasks).",
            snapshot.epic_id,
            len(snapshot.tasks),
        )
        return snapshot

    async def load_or_init(
        self,
        *,
        epic_id: str,
        space_name: str,
    ) -> EpicStateSnapshot:
        """
        Load the snapshot if it exists, otherwise return a fresh empty one.

        Parameters
        ----------
        epic_id:
            The epic identifier (e.g. ``Epic-44445``).
        space_name:
            The project workspace name (e.g. ``AgentSwipe``).

        Returns
        -------
        EpicStateSnapshot
            Existing snapshot from disk or a newly created empty one.
        """
        existing = await self.load()
        if existing is not None:
            return existing

        logger.info("No state.json found for %s — initializing empty snapshot.", epic_id)
        return EpicStateSnapshot(epic_id=epic_id, space_name=space_name)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save(self, snapshot: EpicStateSnapshot) -> None:
        """
        Persist *snapshot* to ``state.json`` atomically.

        Parameters
        ----------
        snapshot:
            The snapshot to write.

        Raises
        ------
        EpicStateError
            On any I/O failure.
        """
        try:
            payload = snapshot.model_dump_json(indent=2)
            await AsyncFileSystemService.write_text_atomic(self._state_path, payload)
        except Exception as exc:
            raise EpicStateError(f"Failed to write state.json at {self._state_path}: {exc}") from exc

        logger.info(
            "Saved epic state for %s — %d task(s) registered.",
            snapshot.epic_id,
            len(snapshot.tasks),
        )

    # ------------------------------------------------------------------
    # Convenience: merge a single task update
    # ------------------------------------------------------------------

    async def update_task_state(
        self,
        snapshot: EpicStateSnapshot,
        task_state: TaskArtifactState,
    ) -> EpicStateSnapshot:
        """
        Merge *task_state* into *snapshot* and persist to disk.

        Produces a new immutable ``EpicStateSnapshot`` rather than mutating
        the existing one, consistent with the immutable-copy pattern used
        throughout the pipeline.

        Parameters
        ----------
        snapshot:
            The current snapshot (returned by :meth:`load_or_init`).
        task_state:
            Updated state for a single task to insert or overwrite.

        Returns
        -------
        EpicStateSnapshot
            The updated snapshot (already persisted to disk).

        Raises
        ------
        EpicStateError
            On any I/O failure during the write.
        """
        updated = snapshot.upsert_task(task_state)
        await self.save(updated)
        logger.debug(
            "[%s] Task %s status → %s (repair_iteration=%d).",
            snapshot.epic_id,
            task_state.task_id,
            task_state.status,
            task_state.repair_iteration,
        )
        return updated


# ---------------------------------------------------------------------------
# Module-level factory helper
# ---------------------------------------------------------------------------


def get_epic_state_service(epic_dir: Path) -> EpicStateService:
    """
    Construct an :class:`EpicStateService` for *epic_dir*.

    Parameters
    ----------
    epic_dir:
        Absolute path to the ``.ebpearls/Epic-{id}/`` directory.

    Returns
    -------
    EpicStateService
    """
    return EpicStateService(epic_dir)

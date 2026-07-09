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

import asyncio
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ebdev.core.exceptions import EpicStateError
from ebdev.core.logger import get_logger
from ebdev.models.task import EpicStateSnapshot, TaskArtifactState

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
# Internal I/O helpers (run in thread pool via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _read_snapshot_sync(state_path: Path) -> Optional[EpicStateSnapshot]:
    """
    Deserialize ``state.json`` from disk synchronously.

    Parameters
    ----------
    state_path:
        Absolute path to the ``state.json`` file.

    Returns
    -------
    EpicStateSnapshot | None
        Parsed snapshot, or ``None`` when the file does not exist.

    Raises
    ------
    EpicStateError
        When the file exists but cannot be parsed (corrupt or schema mismatch).
    """
    if not state_path.exists():
        return None

    try:
        raw = state_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return EpicStateSnapshot.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EpicStateError(f"Failed to parse state.json at {state_path}: {exc}") from exc


def _write_snapshot_sync(state_path: Path, snapshot: EpicStateSnapshot) -> None:
    """
    Serialize *snapshot* to disk atomically via write-then-rename.

    The temporary file is written to the same directory as the target so the
    ``os.rename`` call is guaranteed to be atomic on POSIX systems (same
    filesystem).  On Windows the rename is best-effort.

    Parameters
    ----------
    state_path:
        Absolute path to the target ``state.json`` file.
    snapshot:
        The snapshot to persist.

    Raises
    ------
    EpicStateError
        When the directory cannot be created or the file cannot be written.
    """
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.model_dump_json(indent=2)

        # Write to a sibling temp file, then rename for atomicity.
        fd, tmp_path_str = tempfile.mkstemp(dir=state_path.parent, prefix=".state_", suffix=".json.tmp")
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            tmp_path.rename(state_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    except EpicStateError:
        raise
    except OSError as exc:
        raise EpicStateError(f"Failed to write state.json at {state_path}: {exc}") from exc


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
        snapshot = await asyncio.to_thread(_read_snapshot_sync, self._state_path)
        if snapshot is not None:
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
        await asyncio.to_thread(_write_snapshot_sync, self._state_path, snapshot)
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

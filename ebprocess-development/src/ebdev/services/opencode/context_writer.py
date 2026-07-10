# -*- coding: utf-8 -*-
"""
context_writer.py
=================
EpicContextWriter — owns all context file serialization for the ebdev pipeline.

Architecture
------------
Two-tier context strategy:

Tier 1 — EpicManifest (``context.json``):
    Shared epic-level data written **once** per epic directory.
    Contains: ticket, endpoints, all task_contexts (unfiltered), shared_context,
    starter kits, and project-level metadata.
    Protected by a SHA-256 content-hash sidecar (``.manifest.sha256``) so
    concurrent platform workers never perform redundant disk I/O.

Tier 2 — PlatformContextSlice (``context_{platform}.json``):
    Lean per-platform delta written for every agent invocation.
    Contains: platform, repo_path, active_task_id, current_agent, and a
    **projected** view of task_contexts filtered to tasks assigned to this
    platform only.  No ticket duplication.  No cross-platform noise.

Works identically for both SPOQ (``spoq_epic_dir`` set) and non-SPOQ (legacy)
execution modes — only the directory resolution differs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ebdev.core.logger import get_logger
from ebdev.services.fs import AsyncFileSystemService
from ebdev.services.prompts import to_container_path

if TYPE_CHECKING:
    from ebdev.models.graph_state import JobContext

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Sidecar filename that stores the SHA-256 hash of the last written manifest.
_MANIFEST_HASH_SIDECAR: str = ".manifest.sha256"

#: Filename for the shared epic-level manifest context.
MANIFEST_FILENAME: str = "context.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_sha256(content: str) -> str:
    """Return the SHA-256 hex digest of *content*."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _project_task_contexts(
    shared_context: dict[str, Any],
    platform: str,
) -> dict[str, Any]:
    """
    Return a filtered copy of ``task_contexts`` containing only tasks
    assigned to *platform*.

    Parameters
    ----------
    shared_context:
        The full ``shared_context`` dictionary from ``JobContext``.
    platform:
        The target platform key (e.g. ``"flutter"``, ``"api"``).

    Returns
    -------
    dict[str, Any]
        Filtered ``task_contexts`` — entries where ``platform`` is present
        in the task's ``"platforms"`` list.  Returns empty dict when
        ``shared_context`` contains no ``task_contexts``.
    """
    all_contexts: dict[str, Any] = shared_context.get("task_contexts") or {}
    return {task_id: ctx for task_id, ctx in all_contexts.items() if platform in (ctx.get("platforms") or [])}


def _build_manifest_payload(job_context: "JobContext") -> dict[str, Any]:
    """
    Construct the EpicManifest payload from *job_context*.

    The manifest excludes runtime-mutable fields (``platform``,
    ``repo_path``, ``active_task_id``, ``current_agent``,
    ``validation_errors``) — those belong in the per-platform slice.

    Parameters
    ----------
    job_context:
        The full pipeline job context.

    Returns
    -------
    dict[str, Any]
        JSON-serializable manifest dictionary.
    """
    return {
        "task_id": job_context.task_id,
        "space_name": job_context.space_name,
        "ticket_id": job_context.ticket_id,
        "ticket": job_context.ticket.model_dump(exclude_none=True),
        "platforms": job_context.platforms,
        "branch": job_context.branch,
        "project_repo": job_context.project_repo,
        "starter_kit_urls": job_context.starter_kit_urls,
        "starter_types": job_context.starter_types,
        "linked_ticket_ids": job_context.linked_ticket_ids,
        "feature_name": job_context.feature_name,
        "ticket_label": job_context.ticket_label,
        "mocking_level": job_context.mocking_level,
        "offline_first": job_context.offline_first,
        "spoq_epic_dir": job_context.spoq_epic_dir,
        "map_id": job_context.map_id,
        "shared_context": job_context.shared_context,
    }


def _build_platform_slice_payload(
    job_context: "JobContext",
    platform: str,
) -> dict[str, Any]:
    """
    Construct the per-platform slice payload from *job_context*.

    Contains only fields that are unique to this platform invocation,
    plus a projected (filtered) view of ``task_contexts``.

    Parameters
    ----------
    job_context:
        The full pipeline job context (already narrowed to the target platform
        by the calling node before ``write_context`` is called).
    platform:
        The target platform key.

    Returns
    -------
    dict[str, Any]
        JSON-serializable platform slice dictionary.
    """
    projected_tasks = _project_task_contexts(job_context.shared_context, platform)
    container_repo_path = str(to_container_path(Path(job_context.repo_path)))

    return {
        "platform": platform,
        "repo_path": container_repo_path,
        "active_task_id": job_context.active_task_id,
        "current_agent": job_context.current_agent,
        "mocking_level": job_context.mocking_level,
        "offline_first": job_context.offline_first,
        "task_contexts": projected_tasks,
        "validation_errors": job_context.validation_errors,
    }


# ---------------------------------------------------------------------------
# EpicContextWriter
# ---------------------------------------------------------------------------


class EpicContextWriter:
    """
    Owns all context file serialization for the ebdev pipeline.

    Implements the two-tier context strategy:
    - ``write_epic_manifest`` — shared ``context.json``, written once with
      content-hash idempotency (SHA-256 sidecar guard).
    - ``write_platform_slice`` — lean ``context_{platform}.json``, containing
      only this platform's delta fields and filtered task_contexts.

    Both methods are safe to call concurrently from multiple platform workers
    because the manifest write is guarded by a hash check (only one worker
    actually writes), and each slice write targets a unique filename.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def write_epic_manifest(
        self,
        job_context: "JobContext",
        epic_dir: Path,
    ) -> Path:
        """
        Write the shared ``context.json`` manifest for this epic.

        The write is **idempotent**: a SHA-256 hash of the serialized payload
        is stored in a ``.manifest.sha256`` sidecar.  If the sidecar already
        exists and the hash matches, the manifest file is **not** re-written,
        avoiding redundant disk I/O when multiple platform workers call
        ``write_context`` concurrently.

        Parameters
        ----------
        job_context:
            The full pipeline job context.
        epic_dir:
            The target epic directory (e.g. ``.ebpearls/Epic-39042/``).

        Returns
        -------
        Path
            Absolute path to the written (or existing) ``context.json``.
        """
        manifest_path = epic_dir / MANIFEST_FILENAME
        sidecar_path = epic_dir / _MANIFEST_HASH_SIDECAR

        payload = _build_manifest_payload(job_context)
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        new_hash = _compute_sha256(serialized)

        # Skip re-write if content is unchanged (idempotency guard)
        if await AsyncFileSystemService.exists(sidecar_path):
            try:
                existing_hash = (await AsyncFileSystemService.read_text(sidecar_path)).strip()
                if existing_hash == new_hash:
                    logger.debug(
                        "EpicManifest unchanged (hash=%s). Skipping write: %s",
                        new_hash[:8],
                        manifest_path.name,
                    )
                    return manifest_path
            except Exception as exc:
                logger.warning("Could not read manifest hash sidecar: %s", exc)

        # Write manifest and update sidecar atomically (sidecar last)
        await AsyncFileSystemService.write_text_atomic(manifest_path, serialized)
        await AsyncFileSystemService.write_text_atomic(sidecar_path, new_hash)

        logger.info(
            "EpicManifest written (hash=%s): %s",
            new_hash[:8],
            manifest_path,
        )
        return manifest_path

    async def write_platform_slice(
        self,
        job_context: "JobContext",
        epic_dir: Path,
        platform: str,
    ) -> Path:
        """
        Write the per-platform context slice ``context_{platform}.json``.

        The slice contains only the fields that are unique to this platform
        invocation (``platform``, ``repo_path``, ``active_task_id``,
        ``current_agent``, ``validation_errors``) and a **projected** view
        of ``task_contexts`` — filtered to include only tasks assigned to
        *platform*.

        Parameters
        ----------
        job_context:
            The full pipeline job context, already scoped to the target
            platform by the calling node before ``write_context`` is called).
        epic_dir:
            The target epic directory.
        platform:
            The target platform key (e.g. ``"flutter"``, ``"api"``).

        Returns
        -------
        Path
            Absolute path to the written ``context_{platform}.json``.
        """
        slice_path = epic_dir / f"context_{platform}.json"

        payload = _build_platform_slice_payload(job_context, platform)
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)

        await AsyncFileSystemService.write_text_atomic(slice_path, serialized)
        logger.info(
            "PlatformSlice written (%d task_contexts for '%s'): %s",
            len(payload["task_contexts"]),
            platform,
            slice_path,
        )
        return slice_path

    # ------------------------------------------------------------------
    # Unified entry point (used by OpenCodeService.write_context)
    # ------------------------------------------------------------------

    async def write_context(
        self,
        job_context: "JobContext",
        storage_dir: Path,
        platform: str = "",
    ) -> Path:
        """
        Write the full two-tier context for *job_context*.

        Resolves the epic/task directory, writes the shared manifest (with
        hash guard), and writes the per-platform slice if *platform* is given.

        Works for both SPOQ (``spoq_epic_dir`` set) and non-SPOQ (legacy)
        execution modes.

        Parameters
        ----------
        job_context:
            The full pipeline job context.
        storage_dir:
            Root storage directory (e.g. ``.ebpearls/``).
        platform:
            Optional platform key.  When provided, the platform slice is
            written.  When omitted, only the manifest is written.

        Returns
        -------
        Path
            Path to the per-platform slice if *platform* was given, otherwise
            the manifest path.
        """
        epic_dir = self._resolve_epic_dir(job_context, storage_dir)
        await self.write_epic_manifest(job_context, epic_dir)

        if platform:
            return await self.write_platform_slice(job_context, epic_dir, platform)

        return epic_dir / MANIFEST_FILENAME

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_epic_dir(job_context: "JobContext", storage_dir: Path) -> Path:
        """
        Resolve the target epic directory from *job_context*.

        SPOQ mode:
            ``storage_dir / spoq_epic_dir``
            e.g. ``.ebpearls/Epic-39042/``

        Non-SPOQ (legacy) mode:
            Uses the same task-ID resolution logic previously embedded in
            ``OpenCodeService.write_context`` to keep paths consistent.
            e.g. ``.ebpearls/39042/`` (when task_id = ``"job-39042"``)

        Parameters
        ----------
        job_context:
            The full pipeline job context.
        storage_dir:
            Root storage directory (e.g. ``.ebpearls/``).

        Returns
        -------
        Path
            The resolved epic/task directory.
        """
        if job_context.spoq_epic_dir:
            return storage_dir / job_context.spoq_epic_dir

        # Non-SPOQ path: replicate the existing task_id directory naming logic
        task_id_str = str(job_context.task_id) if getattr(job_context, "task_id", None) else "default"
        if "-" in task_id_str:
            parts = task_id_str.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return storage_dir / parts[1]  # e.g. "39042"
        return storage_dir / task_id_str

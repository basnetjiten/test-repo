# -*- coding: utf-8 -*-
"""
db.py
=====
Database service for job tracking and session management with local fallback.

Responsibilities
----------------
* Update execution status of job and orchestration steps.
* Persist and retrieve OpenCode LLM session tokens.
* Provide local JSON file fallback database when Postgres is unconfigured/unavailable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ebdev.config import config

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState, JobResult

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fallback JSON paths
_SESSIONS_JSON_PATH = Path(config.OPENCODE_PROJECT_DIR) / "sessions.json"
_JOBS_JSON_PATH = Path(config.OPENCODE_PROJECT_DIR) / "jobs.json"


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------
def _ensure_dir_exists(path: Path) -> None:
    """Ensure parent directory of a path exists."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict:
    """Safely load json dictionary from file path."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read JSON at %s: %s", path, e)
        return {}


def _save_json(path: Path, data: dict) -> None:
    """Safely write data to file path as formatted json."""
    _ensure_dir_exists(path)
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, TypeError) as e:
        logger.error("Failed to write JSON at %s: %s", path, e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def update_job_status(result: JobResult) -> bool:
    """
    Update job status in Postgres or fallback JSON.

    Parameters
    ----------
    result : JobResult
        The result schema containing job status updates.

    Returns
    -------
    bool
        True if the update was written successfully.
    """
    if config.POSTGRES_URL:
        try:
            import asyncpg
        except ImportError:
            logger.warning("asyncpg not installed. Falling back to local JSON database.")
        else:
            try:
                conn = await asyncpg.connect(config.POSTGRES_URL)
                try:
                    await conn.execute(
                        """
                        INSERT INTO jobs (
                            job_id, status, summary, errors, warnings, updated_at
                        ) VALUES ($1, $2, $3, $4, $5, NOW())
                        ON CONFLICT (job_id) DO UPDATE 
                        SET status = EXCLUDED.status,
                            summary = EXCLUDED.summary,
                            errors = EXCLUDED.errors,
                            warnings = EXCLUDED.warnings,
                            updated_at = NOW();
                        """,
                        result.job_id,
                        result.status,
                        result.summary,
                        result.errors,
                        result.warnings,
                    )
                    logger.info("Job %s updated in Postgres.", result.job_id)
                    return True
                finally:
                    await conn.close()
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
                logger.error("Postgres update failed: %s. Falling back to local JSON database.", e)

    # Local Fallback
    data = _load_json(_JOBS_JSON_PATH)
    data[result.job_id] = {
        "status": result.status,
        "summary": result.summary,
        "errors": result.errors,
        "warnings": result.warnings,
        "pr_url": result.pr_url,
    }
    _save_json(_JOBS_JSON_PATH, data)
    logger.info("Job %s updated in fallback JSON.", result.job_id)
    return True


async def save_session_id(
    job_id: str, session_id: str, jira_id: str | None = None
) -> bool:
    """
    Persist OpenCode session ID.

    Parameters
    ----------
    job_id : str
        The unique ID of the job execution.
    session_id : str
        The active OpenCode session token.
    jira_id : str | None
        Optional associated JIRA ticket ID.

    Returns
    -------
    bool
        True if the session token was stored.
    """
    if config.POSTGRES_URL:
        try:
            import asyncpg
        except ImportError:
            pass
        else:
            try:
                conn = await asyncpg.connect(config.POSTGRES_URL)
                try:
                    await conn.execute(
                        """
                        INSERT INTO jobs (job_id, opencode_session_id, jira_id, updated_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (job_id) DO UPDATE
                        SET opencode_session_id = EXCLUDED.opencode_session_id,
                            jira_id = COALESCE(EXCLUDED.jira_id, jobs.jira_id),
                            updated_at = NOW();
                        """,
                        job_id,
                        session_id,
                        jira_id,
                    )
                    logger.info("Session ID %r saved for job %s in Postgres.", session_id, job_id)
                    return True
                finally:
                    await conn.close()
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
                logger.error("Postgres session save failed: %s.", e)

    # Fallback
    data = _load_json(_SESSIONS_JSON_PATH)
    data[job_id] = {
        "opencode_session_id": session_id,
        "jira_id": jira_id,
    }
    _save_json(_SESSIONS_JSON_PATH, data)
    logger.info("Session ID %r saved for job %s in fallback JSON.", session_id, job_id)
    return True


async def get_session_id(job_id: str) -> str | None:
    """
    Retrieve stored OpenCode session ID for a job.

    Parameters
    ----------
    job_id : str
        The unique job ID.

    Returns
    -------
    str | None
        The session ID if found, or None.
    """
    if config.POSTGRES_URL:
        try:
            import asyncpg
        except ImportError:
            pass
        else:
            try:
                conn = await asyncpg.connect(config.POSTGRES_URL)
                try:
                    row = await conn.fetchrow(
                        "SELECT opencode_session_id FROM jobs WHERE job_id = $1;",
                        job_id,
                    )
                    return row["opencode_session_id"] if row else None
                finally:
                    await conn.close()
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
                logger.error("Postgres session fetch failed: %s.", e)

    # Fallback
    data = _load_json(_SESSIONS_JSON_PATH)
    job_info = data.get(job_id)
    return job_info.get("opencode_session_id") if job_info else None


async def get_session_id_by_jira_id(ticket_id: str) -> str | None:
    """
    Retrieve the most recent session ID for a ticket ID.

    Parameters
    ----------
    ticket_id : str
        The ticket/issue ID associated with the session.

    Returns
    -------
    str | None
        The most recent matching session token.
    """
    if config.POSTGRES_URL:
        try:
            import asyncpg
        except ImportError:
            pass
        else:
            try:
                conn = await asyncpg.connect(config.POSTGRES_URL)
                try:
                    row = await conn.fetchrow(
                        """
                        SELECT opencode_session_id FROM jobs
                        WHERE jira_id = $1 AND opencode_session_id IS NOT NULL
                        ORDER BY created_at DESC LIMIT 1;
                        """,
                        ticket_id,
                    )
                    return row["opencode_session_id"] if row else None
                finally:
                    await conn.close()
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
                logger.error("Postgres session fetch by ticket ID failed: %s.", e)

    # Fallback
    data = _load_json(_SESSIONS_JSON_PATH)
    for job_id, val in data.items():
        if val.get("jira_id") == ticket_id and val.get("opencode_session_id"):
            return val.get("opencode_session_id")
    return None


async def sync_state_to_db(state: GraphState) -> bool:
    """
    Update job record with current execution progress message.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    bool
        True if database sync succeeded.
    """
    if config.POSTGRES_URL:
        try:
            import asyncpg
        except ImportError:
            pass
        else:
            try:
                conn = await asyncpg.connect(config.POSTGRES_URL)
                try:
                    status = "in_progress"
                    if state.done:
                        status = "complete"
                    if state.failed:
                        status = "failed"

                    await conn.execute(
                        """
                        INSERT INTO jobs (
                            job_id, status, summary, updated_at
                        ) VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (job_id) DO UPDATE 
                        SET status = EXCLUDED.status,
                            summary = EXCLUDED.summary,
                            updated_at = NOW();
                        """,
                        state.context.ticket_id,
                        status,
                        state.status_message,
                    )
                    return True
                finally:
                    await conn.close()
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
                logger.error("Postgres state sync failed: %s.", e)

    # Fallback
    data = _load_json(_JOBS_JSON_PATH)
    status = "in_progress"
    if state.done:
        status = "complete"
    if state.failed:
        status = "failed"

    data[state.context.ticket_id] = {
        "status": status,
        "summary": state.status_message,
        "validation_errors": state.context.validation_errors,
        "last_node": state.last_node,
    }
    _save_json(_JOBS_JSON_PATH, data)
    return True

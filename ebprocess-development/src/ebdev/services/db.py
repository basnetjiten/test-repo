# -*- coding: utf-8 -*-
"""Database service for job tracking and session management with local fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
from ebdev.config import config
from ebdev.core.logger import get_logger
from ebdev.models.schemas import GraphState, JobResult

logger = get_logger(__name__)

# Fallback JSON paths
_SESSIONS_JSON_PATH = Path(config.OPENCODE_PROJECT_DIR) / "sessions.json"
_JOBS_JSON_PATH = Path(config.OPENCODE_PROJECT_DIR) / "jobs.json"


def _ensure_dir_exists(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to read JSON at {path}: {e}")
        return {}


def _save_json(path: Path, data: dict) -> None:
    _ensure_dir_exists(path)
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write JSON at {path}: {e}")


async def update_job_status(result: JobResult) -> bool:
    """Update job status in Postgres or fallback JSON."""
    if config.POSTGRES_URL:
        try:
            import asyncpg
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
                logger.info(f"Job {result.job_id} updated in Postgres.")
                return True
            finally:
                await conn.close()
        except ImportError:
            logger.warning("asyncpg not installed. Falling back to local JSON database.")
        except Exception as e:
            logger.error(f"Postgres update failed: {e}. Falling back to local JSON database.")

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
    logger.info(f"Job {result.job_id} updated in fallback JSON.")
    return True


async def save_session_id(
    job_id: str, session_id: str, jira_id: str | None = None
) -> bool:
    """Persist OpenCode session ID."""
    if config.POSTGRES_URL:
        try:
            import asyncpg
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
                logger.info(f"Session ID {session_id!r} saved for job {job_id} in Postgres.")
                return True
            finally:
                await conn.close()
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Postgres session save failed: {e}.")

    # Fallback
    data = _load_json(_SESSIONS_JSON_PATH)
    data[job_id] = {
        "opencode_session_id": session_id,
        "jira_id": jira_id,
    }
    _save_json(_SESSIONS_JSON_PATH, data)
    logger.info(f"Session ID {session_id!r} saved for job {job_id} in fallback JSON.")
    return True


async def get_session_id(job_id: str) -> str | None:
    """Retrieve stored OpenCode session ID for a job."""
    if config.POSTGRES_URL:
        try:
            import asyncpg
            conn = await asyncpg.connect(config.POSTGRES_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT opencode_session_id FROM jobs WHERE job_id = $1;",
                    job_id,
                )
                return row["opencode_session_id"] if row else None
            finally:
                await conn.close()
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Postgres session fetch failed: {e}.")

    # Fallback
    data = _load_json(_SESSIONS_JSON_PATH)
    job_info = data.get(job_id)
    return job_info.get("opencode_session_id") if job_info else None


async def get_session_id_by_jira_id(jira_id: str) -> str | None:
    """Retrieve the most recent session ID for a Jira ticket ID."""
    if config.POSTGRES_URL:
        try:
            import asyncpg
            conn = await asyncpg.connect(config.POSTGRES_URL)
            try:
                row = await conn.fetchrow(
                    """
                    SELECT opencode_session_id FROM jobs
                    WHERE jira_id = $1 AND opencode_session_id IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    jira_id,
                )
                return row["opencode_session_id"] if row else None
            finally:
                await conn.close()
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Postgres session fetch by jira failed: {e}.")

    # Fallback
    data = _load_json(_SESSIONS_JSON_PATH)
    for job_id, val in data.items():
        if val.get("jira_id") == jira_id and val.get("opencode_session_id"):
            return val.get("opencode_session_id")
    return None


async def sync_state_to_db(state: GraphState) -> bool:
    """Update job record with current execution progress message."""
    if config.POSTGRES_URL:
        try:
            import asyncpg
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
                    state.context.jira_ticket_id,
                    status,
                    state.status_message,
                )
                return True
            finally:
                await conn.close()
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Postgres state sync failed: {e}.")

    # Fallback
    data = _load_json(_JOBS_JSON_PATH)
    status = "in_progress"
    if state.done:
        status = "complete"
    if state.failed:
        status = "failed"

    data[state.context.jira_ticket_id] = {
        "status": status,
        "summary": state.status_message,
        "validation_errors": state.context.validation_errors,
        "last_node": state.last_node,
    }
    _save_json(_JOBS_JSON_PATH, data)
    return True

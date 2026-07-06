# -*- coding: utf-8 -*-
"""
finalize.py
===========
Finalize node - performs cleanup, records final status, and runs callbacks.

Responsibilities
----------------
* Consolidate final JobResult.
* Persist status updates in database.
* Notify execution callbacks via HTTP POST.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import httpx

from ebdev.models.schemas import JobResult
from ebdev.services import db

if TYPE_CHECKING:
    from ebdev.models.schemas import GraphState

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node Entry Point
# ---------------------------------------------------------------------------
async def finalize_node(state: GraphState) -> GraphState:
    """
    Consolidate the final JobResult, persist status updates, and notify execution callbacks.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    GraphState
        The updated state with the finalized job result and done flag set.
    """
    state.last_node = "finalize"
    start_time = time.time()
    ctx = state.context
    callback_url = ctx.n8n_callback_url

    logger.info("Finishing job: %s", ctx.ticket_id)

    status = "success" if not state.failed else "failed"
    summary = state.result.summary if state.result else "Job completed."

    result = JobResult(
        task_id=ctx.ticket_id,
        space_name=ctx.space_name,
        ticket_id=ctx.ticket.id if ctx.ticket else ctx.ticket_id,
        status=status,
        summary=summary,
        warnings=[],
        errors=[],
        pr_url=state.result.pr_url if state.result else (state.pull_request_url or None),
    )

    # 1. Update fallback status
    try:
        await db.update_job_status(result)
    except (OSError, ValueError) as e:
        logger.error("DB persistence failed: %s", e)

    # 2. Callback webhook
    if not callback_url:
        logger.info("No callback URL for job %s - skipping callback.", ctx.ticket_id)
    else:
        logger.info("Posting result to callback URL: %s", callback_url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                data = result.model_dump()
                response = await client.post(callback_url, json=data)
                response.raise_for_status()
                logger.info("Callback successful: %d", response.status_code)
        except httpx.HTTPError as e:
            logger.warning("Callback failed: %s", e)

    duration = round(time.time() - start_time, 2)
    logger.info("Done in %ss.", duration)
    logger.info("Job %s finished with status: %s.", ctx.ticket_id, status)

    return state.model_copy(update={
        "last_node": "finalize",
        "result": result,
        "done": True,
    })

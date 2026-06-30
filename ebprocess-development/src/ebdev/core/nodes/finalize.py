# -*- coding: utf-8 -*-
"""Finalize node - performs cleanup, records final status, and runs callbacks."""

from __future__ import annotations

import time
import httpx

from ebdev.models.schemas import GraphState, JobResult
from ebdev.services import db


async def finalize_node(state: GraphState) -> GraphState:
    """Consolidate the final JobResult, persist status updates, and notify execution callbacks."""
    state.last_node = "finalize"
    start_time = time.time()
    ctx = state.context
    callback_url = ctx.n8n_callback_url
    
    print(f"[finalize] Finishing job: {ctx.jira_ticket_id}")
    
    status = "success" if not state.failed else "failed"
    summary = state.result.summary if state.result else "Job completed."
    
    result = JobResult(
        job_id=ctx.jira_ticket_id,
        jira_space_name=ctx.jira_space_name,
        jira_id=ctx.jira_ticket.id if ctx.jira_ticket else ctx.jira_ticket_id,
        status=status,
        summary=summary,
        warnings=[],
        errors=[],
        pr_url=state.result.pr_url if state.result else (state.pull_request_url or None),
    )
    
    # 1. Update fallback status
    try:
        await db.update_job_status(result)
    except Exception as e:
        print(f"[finalize] DB persistence failed: {e}")

    # 2. Callback webhook
    if not callback_url:
        print(f"[finalize] No callback URL for job {ctx.jira_ticket_id} - skipping callback.")
    else:
        print(f"[finalize] Posting result to callback URL: {callback_url}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                data = result.model_dump()
                response = await client.post(callback_url, json=data)
                response.raise_for_status()
                print(f"[finalize] Callback successful: {response.status_code}")
        except Exception as e:
            print(f"[finalize] WARNING: callback failed: {e}")

    duration = round(time.time() - start_time, 2)
    print(f"[finalize] Done in {duration}s.")
    print(f"[finalize] Job {ctx.jira_ticket_id} finished with status: {status}.")

    return state.model_copy(update={
        "last_node": "finalize",
        "result": result,
        "done": True,
    })

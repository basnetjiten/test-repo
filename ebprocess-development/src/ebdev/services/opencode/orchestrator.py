# -*- coding: utf-8 -*-
"""
orchestrator.py
===============
OpenCode agent orchestration service.

Coordinates the full lifecycle of an OpenCode deepagent invocation:
  - Session creation and resumption
  - Server-Sent Events (SSE) streaming
  - Prompt dispatch and response parsing
  - Context file delegation (via EpicContextWriter)

Backward-compatibility wrappers (``invoke_opencode``, ``write_context``) are
exported at the bottom for callers that reference the old function signatures.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import httpx
from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents
from ebdev.core.logger import get_logger
from ebdev.core.throttle import CircuitBreakerOpenError
from ebdev.models.graph_state import JobResult
from ebdev.services import prompts
from ebdev.services.opencode.client import (
    DEFAULT_OPENCODE_SERVER_URL,
    OpenCodeAPIClient,
    extract_json_block,
)
from ebdev.services.opencode.context_writer import EpicContextWriter
from ebdev.services.prompts import to_container_path

if TYPE_CHECKING:
    from ebdev.models.graph_state import JobContext

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Orchestrator Service
# ---------------------------------------------------------------------------
class OpenCodeService:
    """Orchestration service coordinating execution context, prompts and deepagent APIs."""

    @staticmethod
    async def write_context(job_context: JobContext, storage_dir: Path, platform: str = "") -> Path:
        """
        Write the two-tier context files for *job_context*.

        Delegates to :class:`~ebdev.services.opencode.context_writer.EpicContextWriter`
        which implements:

        - **Tier 1** — shared ``context.json`` (EpicManifest), written once
          per epic with SHA-256 content-hash idempotency.
        - **Tier 2** — lean ``context_{platform}.json`` (PlatformSlice),
          containing only this platform's delta fields and filtered
          ``task_contexts``.

        Works for both SPOQ and non-SPOQ execution modes.

        Parameters
        ----------
        job_context:
            The full pipeline job context.
        storage_dir:
            Root storage directory (e.g. ``.ebpearls/``).
        platform:
            Target platform key.  When provided, the platform slice is written
            in addition to the manifest.

        Returns
        -------
        Path
            Path to the platform slice when *platform* is given; otherwise the
            manifest path.
        """

        return await EpicContextWriter().write_context(job_context, storage_dir, platform=platform)

    @staticmethod
    def _resolve_agent(job_context: JobContext) -> str:
        """Resolve generic platform task intent to specific agent identifier keys."""
        phase_map = {
            "flutter": {"plan": Agents.FLUTTER_PLANNER, "build": Agents.FLUTTER_BUILDER},
            "api": {"plan": Agents.API_PLANNER, "build": Agents.API_BUILDER},
            "web": {"plan": Agents.WEB_PLANNER, "build": Agents.WEB_BUILDER},
            "cms": {"plan": Agents.CMS_PLANNER, "build": Agents.CMS_BUILDER},
        }
        agent_key = job_context.current_agent
        return phase_map.get(job_context.platform, {}).get(agent_key, agent_key)

    @classmethod
    async def invoke_async(
        cls,
        job_context: JobContext,
        progress_callback: Callable[[str], None] | None = None,
        session_id: str | None = None,
    ) -> tuple[JobResult, str | None]:
        """
        Invoke OpenCode deepagent server asynchronously.

        Parameters
        ----------
        job_context : JobContext
            The active job configuration context.
        progress_callback : Callable[[str], None] | None
            Optional callback mapping execution status alerts.
        session_id : str | None
            Optional active session ID to resume.

        Returns
        -------
        tuple[JobResult, str | None]
            A tuple containing the JobResult schema and the session ID.
        OpenCode failures are converted into a failed JobResult so the graph
        can continue and report the error without aborting the run.
        """
        agent = cls._resolve_agent(job_context)
        platform = job_context.platform
        # Project-scoped storage: .opencode/<space_name>/
        storage_dir = job_context.project_storage_dir(config.OPENCODE_PROJECT_DIR)

        await cls.write_context(job_context, storage_dir, platform=platform)
        prompt = prompts.build_prompt(job_context, storage_dir=storage_dir, session_id=session_id, platform=platform)

        server_url = config.OPENCODE_SERVER_URL or DEFAULT_OPENCODE_SERVER_URL
        directory = str(
            to_container_path(Path(job_context.repo_path or Path(config.WORKSPACE_DIR) / job_context.space_name))
        )
        client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY, directory=directory)

        # 1. Initialize session if not resuming
        if not session_id:
            try:
                session_id = await client.create_session(title=f"Job {job_context.ticket_id}")
            except (httpx.HTTPError, CircuitBreakerOpenError, json.JSONDecodeError, KeyError) as e:
                logger.error("Failed to create agent execution session: %s", e)
                return JobResult(
                    task_id=job_context.ticket_id,
                    space_name=job_context.space_name,
                    ticket_id=job_context.ticket_id,
                    status="failed",
                    summary=f"OpenCode session creation failed: {e}",
                    errors=[str(e)],
                ), None

        # 2. Concurrently read Server-Sent Events (SSE) to print deltas
        def _handle_delta(event: dict[str, Any]) -> None:
            if delta := event.get("properties", {}).get("delta"):
                logger.debug(delta)

        def _handle_step_start(event: dict[str, Any]) -> None:
            part_id = event.get("part", {}).get("id", "Unknown")
            logger.info("\n[OpenCode Step Start] %s", part_id)
            if progress_callback:
                progress_callback(f"Step Started: {part_id}")

        def _handle_step_finish(event: dict[str, Any]) -> None:
            part_id = event.get("part", {}).get("id", "Unknown")
            logger.info("\n[OpenCode Step Finish] %s", part_id)

        event_handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "message.part.delta": _handle_delta,
            "step-start": _handle_step_start,
            "step-finish": _handle_step_finish,
        }

        async def event_streamer() -> None:
            try:
                async for event in client.stream_events():
                    evt_type = event.get("type")
                    if handler := event_handlers.get(evt_type):  # type: ignore[arg-type]
                        handler(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("SSE event stream handler encountered an error: %s", e)

        stream_task = asyncio.create_task(event_streamer())
        await asyncio.sleep(0.1)  # Let streaming listener subscribe

        # 3. Post prompt instructions and wait for resolution
        try:
            msg_data = await client.send_prompt_message(
                session_id=session_id,
                agent=agent,
                prompt=prompt,
                model=config.OPENCODE_MODEL,
            )
        except (httpx.HTTPError, CircuitBreakerOpenError, json.JSONDecodeError) as e:
            # Check if this is a 404 error indicating a stale/missing session
            is_404 = False
            response = getattr(e, "response", None)
            if (response is not None and getattr(response, "status_code", None) == 404) or (
                isinstance(e, httpx.HTTPError) and ("404" in str(e) or "Not Found" in str(e))
            ):
                is_404 = True

            if is_404 and session_id:
                logger.warning(
                    "Session %s not found on server (404 status). Falling back to a new session...",
                    session_id,
                )
                try:
                    session_id = await client.create_session(title=f"Job {job_context.ticket_id}")
                    msg_data = await client.send_prompt_message(
                        session_id=session_id,
                        agent=agent,
                        prompt=prompt,
                        model=config.OPENCODE_MODEL,
                    )
                except (httpx.HTTPError, CircuitBreakerOpenError, json.JSONDecodeError, KeyError) as retry_err:
                    logger.error("Failed to execute agent instructions on new session: %s", retry_err)
                    return JobResult(
                        task_id=job_context.ticket_id,
                        space_name=job_context.space_name,
                        ticket_id=job_context.ticket_id,
                        status="failed",
                        summary=f"OpenCode execution failed: {retry_err}",
                        errors=[str(retry_err)],
                    ), session_id
            else:
                logger.error("Failed to execute agent instructions on server: %s", e)
                return JobResult(
                    task_id=job_context.ticket_id,
                    space_name=job_context.space_name,
                    ticket_id=job_context.ticket_id,
                    status="failed",
                    summary=f"OpenCode execution failed: {e}",
                    errors=[str(e)],
                ), session_id
        finally:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)

        # 4. Extract reply text and build return state schemas
        parts = msg_data.get("parts", [])
        reply_text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")

        logger.info("Agent execution complete on session: %s", session_id)
        target_ids = [job_context.ticket_id]
        if job_context.active_task_id:
            target_ids.append(job_context.active_task_id)

        data = extract_json_block(reply_text, target_ids)

        if data:
            try:
                logger.info("Parsed successful agent metadata block.")
                # Assure base context elements are set in return payload
                data.setdefault("space_name", job_context.space_name)
                data.setdefault("ticket_id", job_context.ticket_id)
                data.setdefault("task_id", job_context.ticket_id)
                if "job_id" in data:
                    data["task_id"] = data.pop("job_id")
                data.setdefault("status", "success")
                return JobResult(**data), session_id
            except (ValueError, ValidationError) as e:
                logger.warning("Metadata format parsing warnings: %s", e)

        # Final fallback status - if we couldn't parse a structured JSON block, it is a failure
        return JobResult(
            task_id=job_context.ticket_id,
            space_name=job_context.space_name,
            ticket_id=job_context.ticket_id,
            status="failed",
            summary="Agent failed to return a valid status JSON metadata block.",
            errors=["No valid JSON metadata block extracted from agent reply text."],
        ), session_id

    @classmethod
    def invoke(
        cls,
        job_context: JobContext,
        progress_callback: Callable[[str], None] | None = None,
        session_id: str | None = None,
    ) -> tuple[JobResult, str | None]:
        """
        Synchronously execute the target agent in an isolated event thread.

        Parameters
        ----------
        job_context : JobContext
            The active job configuration context.
        progress_callback : Callable[[str], None] | None
            Optional status message dispatcher.
        session_id : str | None
            Optional active session ID to resume.

        Returns
        -------
        tuple[JobResult, str | None]
            The output JobResult and active session ID.
        """

        def run_in_new_loop():
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(cls.invoke_async(job_context, progress_callback, session_id))
            finally:
                new_loop.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_new_loop)
            return future.result()


# ---------------------------------------------------------------------------
# Backward Compatibility Wrappers
# ---------------------------------------------------------------------------
def invoke_opencode(*args, **kwargs):
    """Wrapper matching original function signature for backward compatibility."""
    return OpenCodeService.invoke(*args, **kwargs)


async def write_context(*args, **kwargs):
    """Wrapper matching original function signature for backward compatibility."""
    return await OpenCodeService.write_context(*args, **kwargs)

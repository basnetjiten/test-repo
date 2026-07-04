# -*- coding: utf-8 -*-
"""
opencode.py
===========
OpenCode deepagent HTTP API runner and orchestrator for ebprocess-development.

Responsibilities
----------------
* Client wrapper for communication with the headless OpenCode deepagent server.
* Orchestrate execution context, prompts, and deepagent APIs.
* Stream and capture real-time Server-Sent Events (SSE) from the deepagent engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

import httpx
from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents, RegexPatterns
from ebdev.models.schemas import JobResult
from ebdev.services import prompts
from ebdev.services.prompts import to_container_path

if TYPE_CHECKING:
    from ebdev.models.schemas import JobContext

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fallback OpenCode server URL if not configured in environmental settings.
DEFAULT_OPENCODE_SERVER_URL: str = "http://opencode:4096"


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------
def extract_figma_url(description: str) -> str | None:
    """
    Return the first Figma design URL found in the description, or None.

    Parameters
    ----------
    description : str
        The text containing potential Figma design URLs.

    Returns
    -------
    str | None
        The URL string if found, or None.
    """
    if not description:
        return None
    match = re.search(RegexPatterns.FIGMA_URL, description)
    return match.group(0) if match else None


def extract_json_block(text: str, job_id: str) -> dict | None:
    """
    Parse the final message text to extract the agent's JobResult JSON structure.

    Parameters
    ----------
    text : str
        The raw reply text from the agent.
    job_id : str
        The job or ticket ID to match within the JSON.

    Returns
    -------
    dict | None
        Parsed result dictionary if successfully extracted and validated, or None.
    """
    if not text:
        return None

    # Strategy 1: Look for standard markdown ```json blocks
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, dict) and (data.get("job_id") == job_id or data.get("ticket_id") == job_id or data.get("jira_id") == job_id):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 2: Fallback to scanning for raw outer-braces { ... }
    try:
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            data = json.loads(text[start_idx:end_idx + 1])
            if isinstance(data, dict) and (data.get("job_id") == job_id or data.get("ticket_id") == job_id or data.get("jira_id") == job_id):
                return data
    except (json.JSONDecodeError, TypeError):
        pass

    return None


# ---------------------------------------------------------------------------
# Model resolution helpers
# ---------------------------------------------------------------------------


def _build_model_payload(model: str | dict[str, Any] | None) -> dict[str, str] | None:
    """
    Resolve a model string or dict into an OpenCode ``{providerID, modelID}`` payload.

    Parameters
    ----------
    model : str | dict | None
        The model configuration. If a string with a ``/`` separator, the prefix
        is used as the providerID directly (e.g. ``"openai/gpt-4o"``). If a plain
        string without a slash, it cannot be resolved and returns ``None``. If a
        ``dict``, it is returned unchanged. ``None`` returns ``None``.

    Returns
    -------
    dict[str, str] | None
        A ``{"providerID": ..., "modelID": ...}`` dict, or ``None`` if unresolvable.
    """
    if not model:
        return None

    if isinstance(model, dict):
        return model  # type: ignore[return-value]

    model_str = model.strip()

    # Explicit provider/model notation takes priority (e.g. "anthropic/claude-sonnet-4-5")
    if "/" in model_str:
        provider_id, model_id = model_str.split("/", 1)
        return {"providerID": provider_id, "modelID": model_id}

    logger.warning(
        "Could not resolve provider ID for model '%s'. "
        "Omitting model to use server default. "
        "Use 'provider/model' format (e.g. 'anthropic/claude-sonnet-4-5') to be explicit.",
        model_str,
    )
    return None


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------
class OpenCodeAPIClient:
    """Client wrapper for communication with the headless OpenCode deepagent server."""

    def __init__(self, base_url: str, api_key: str | None = None, directory: str | None = None):
        """
        Initialize the OpenCode API client.

        Parameters
        ----------
        base_url : str
            The base URL of the OpenCode server.
        api_key : str | None
            Optional API authorization token.
        directory : str | None
            Project directory used to scope OpenCode requests.
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.directory = directory

    def _params(self) -> dict[str, str] | None:
        """Return request query parameters for the configured project directory."""
        if not self.directory:
            return None
        return {"directory": self.directory}

    async def check_health(self) -> dict:
        """
        Fetch endpoint connectivity and engine verification health status.

        Returns
        -------
        dict
            Health check JSON data.
        """
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{self.base_url}/global/health", headers=self.headers, params=self._params())
            res.raise_for_status()
            return res.json()

    async def create_session(self, title: str | None = None) -> str:
        """
        Create a new agent session on the server.

        Parameters
        ----------
        title : str | None
            Optional description title for the session.

        Returns
        -------
        str
            The created session ID.
        """
        async with httpx.AsyncClient() as client:
            payload = {"title": title} if title else {}
            headers = {**self.headers}
            if self.directory:
                headers["x-opencode-directory"] = quote(self.directory, safe="")
            res = await client.post(f"{self.base_url}/session", json=payload, headers=headers, params=self._params())
            res.raise_for_status()
            return res.json()["id"]

    async def send_prompt_message(
        self, session_id: str, agent: str, prompt: str, model: str | dict[str, Any] | None = None
    ) -> dict:
        """
        Post prompt instruction message to execution session and await output.

        Parameters
        ----------
        session_id : str
            The target execution session token.
        agent : str
            The agent role name to dispatch.
        prompt : str
            The prompt message instructions.
        model : str | dict[str, Any] | None
            Optional LLM model override name or model configuration object.

        Returns
        -------
        dict
            The raw JSON response dictionary.
        """
        payload: dict[str, Any] = {
            "agent": agent,
            "parts": [{"type": "text", "text": prompt}],
        }
        resolved_model = _build_model_payload(model)
        if resolved_model:
            payload["model"] = resolved_model

        async with httpx.AsyncClient(timeout=None) as client:
            res = await client.post(
                f"{self.base_url}/session/{session_id}/message",
                json=payload,
                headers=self.headers,
                params=self._params(),
            )
            res.raise_for_status()
            return res.json()

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream real-time server-sent events (SSE).

        Yields
        ------
        dict[str, Any]
            The parsed SSE event payload.
        """
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{self.base_url}/event", headers=self.headers, params=self._params()) as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            yield json.loads(line[5:])
                        except json.JSONDecodeError:
                            pass


# ---------------------------------------------------------------------------
# Orchestrator Service
# ---------------------------------------------------------------------------
class OpenCodeService:
    """Orchestration service coordinating execution context, prompts and deepagent APIs."""

    @staticmethod
    def write_context(job_context: JobContext, storage_dir: Path, platform: str = "") -> Path:
        """
        Serialize JobContext to a platform-prefixed context JSON descriptor in storage_dir/tasks/.

        Parameters
        ----------
        job_context : JobContext
            The job configuration context schemas.
        storage_dir : Path
            The central .opencode storage directory.
        platform : str
            The platform identifier used to prefix the context filename.

        Returns
        -------
        Path
            The path of the written JSON context file.
        """
        if not job_context.ticket.figma_url:
            url = extract_figma_url(job_context.ticket.description)
            if url:
                job_context.ticket.figma_url = url

        tasks_dir = storage_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        prefix = f"{job_context.job_id}_" if getattr(job_context, "job_id", None) else ""
        filename = f"{prefix}{platform}_context.json" if platform else f"{prefix}context.json"
        ctx_file = tasks_dir / filename
        serializable_context = job_context.model_copy(
            update={
                "repo_path": str(to_container_path(Path(job_context.repo_path))),
                "spoq_epic_dir": str(to_container_path(Path(job_context.spoq_epic_dir))) if job_context.spoq_epic_dir else None,
            }
        )
        ctx_file.write_text(serializable_context.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
        return ctx_file

    @staticmethod
    def _resolve_agent(job_context: JobContext) -> str:
        """Resolve generic platform task intent to specific agent identifier keys."""
        phase_map = {
            "flutter": {"plan": Agents.FLUTTER_PLANNER, "build": Agents.FLUTTER_BUILDER},
            "api": {"plan": "api_planner", "build": "api_builder"},
            "web": {"plan": "web_planner", "build": "web_builder"},
            "cms": {"plan": "cms_planner", "build": "cms_builder"},
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

        # Ensure tasks directory exists (plans live directly in storage_dir)
        (storage_dir / "tasks").mkdir(parents=True, exist_ok=True)

        cls.write_context(job_context, storage_dir, platform=platform)
        prompt = prompts.build_prompt(job_context, storage_dir=storage_dir, session_id=session_id, platform=platform)

        server_url = config.OPENCODE_SERVER_URL or DEFAULT_OPENCODE_SERVER_URL
        directory = str(to_container_path(Path(job_context.repo_path or Path(config.WORKSPACE_DIR) / job_context.space_name)))
        client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY, directory=directory)
        
        # 1. Initialize session if not resuming
        if not session_id:
            try:
                session_id = await client.create_session(title=f"Job {job_context.ticket_id}")
            except Exception as e:
                logger.error("Failed to create agent execution session: %s", e)
                return JobResult(
                    job_id=job_context.ticket_id,
                    space_name=job_context.space_name,
                    ticket_id=job_context.ticket_id,
                    status="failed",
                    summary=f"OpenCode session creation failed: {e}",
                    errors=[str(e)],
                ), None

        # 2. Concurrently read Server-Sent Events (SSE) to print deltas
        def _handle_delta(event: dict[str, Any]) -> None:
            if delta := event.get("properties", {}).get("delta"):
                print(delta, end="", flush=True)

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
        except Exception as e:
            logger.error("Failed to execute agent instructions on server: %s", e)
            return JobResult(
                job_id=job_context.ticket_id,
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
        data = extract_json_block(reply_text, job_context.ticket_id)

        if data:
            try:
                logger.info("Parsed successful agent metadata block.")
                # Assure base context elements are set in return payload
                data.setdefault("space_name", job_context.space_name)
                data.setdefault("ticket_id", job_context.ticket_id)
                data.setdefault("job_id", job_context.ticket_id)
                data.setdefault("status", "success")
                return JobResult(**data), session_id
            except (ValueError, ValidationError) as e:
                logger.warning("Metadata format parsing warnings: %s", e)

        # Final fallback status
        return JobResult(
            job_id=job_context.ticket_id,
            space_name=job_context.space_name,
            ticket_id=job_context.ticket_id,
            status="success",
            summary="Operation completed successfully.",
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
                return new_loop.run_until_complete(
                    cls.invoke_async(job_context, progress_callback, session_id)
                )
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


def write_context(*args, **kwargs):
    """Wrapper matching original function signature for backward compatibility."""
    return OpenCodeService.write_context(*args, **kwargs)

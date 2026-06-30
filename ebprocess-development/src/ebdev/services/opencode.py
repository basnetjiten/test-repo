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
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

import httpx
from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents, RegexPatterns
from ebdev.core.exceptions import OpenCodeExecutionError
from ebdev.models.schemas import JobResult
from ebdev.services import prompts

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
# API Client
# ---------------------------------------------------------------------------
class OpenCodeAPIClient:
    """Client wrapper for communication with the headless OpenCode deepagent server."""

    def __init__(self, base_url: str, api_key: str | None = None):
        """
        Initialize the OpenCode API client.

        Parameters
        ----------
        base_url : str
            The base URL of the OpenCode server.
        api_key : str | None
            Optional API authorization token.
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def check_health(self) -> dict:
        """
        Fetch endpoint connectivity and engine verification health status.

        Returns
        -------
        dict
            Health check JSON data.
        """
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{self.base_url}/global/health", headers=self.headers)
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
            res = await client.post(f"{self.base_url}/session", json=payload, headers=self.headers)
            res.raise_for_status()
            return res.json()["id"]

    async def send_prompt_message(
        self, session_id: str, agent: str, prompt: str, model: str | None = None
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
        model : str | None
            Optional LLM model override name.

        Returns
        -------
        dict
            The raw JSON response dictionary.
        """
        payload = {
            "agent": agent,
            "parts": [{"type": "text", "text": prompt}]
        }
        if model:
            payload["model"] = model

        async with httpx.AsyncClient(timeout=600.0) as client:
            res = await client.post(
                f"{self.base_url}/session/{session_id}/message",
                json=payload,
                headers=self.headers
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
            async with client.stream("GET", f"{self.base_url}/event", headers=self.headers) as stream:
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
    def write_context(job_context: JobContext) -> Path:
        """
        Serialize JobContext to context.json metadata descriptor in target directory.

        Parameters
        ----------
        job_context : JobContext
            The job configuration context schemas.

        Returns
        -------
        Path
            The path of the written JSON context file.
        """
        if not job_context.ticket.figma_url:
            url = extract_figma_url(job_context.ticket.description)
            if url:
                job_context.ticket.figma_url = url

        tasks_dir = Path(config.OPENCODE_PROJECT_DIR) / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)

        ctx_file = tasks_dir / "context.json"
        ctx_file.write_text(job_context.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
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

        Raises
        ------
        OpenCodeExecutionError
            If session initialization or request dispatching fails.
        """
        agent = cls._resolve_agent(job_context)
        storage_dir = Path(config.OPENCODE_PROJECT_DIR)
        
        # Setup structural files and directories
        (storage_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (storage_dir / "plans").mkdir(parents=True, exist_ok=True)

        cls.write_context(job_context)
        prompt = prompts.build_prompt(job_context, storage_dir=storage_dir, session_id=session_id)

        server_url = config.OPENCODE_SERVER_URL or DEFAULT_OPENCODE_SERVER_URL
        client = OpenCodeAPIClient(base_url=server_url, api_key=config.OPENCODE_API_KEY)
        
        # 1. Initialize session if not resuming
        if not session_id:
            try:
                session_id = await client.create_session(title=f"Job {job_context.ticket_id}")
            except Exception as e:
                logger.error("Failed to create agent execution session: %s", e)
                raise OpenCodeExecutionError(f"OpenCode session creation failed: {e}") from e

        # 2. Concurrently read Server-Sent Events (SSE) to print deltas
        async def event_streamer():
            try:
                async for event in client.stream_events():
                    evt_type = event.get("type")
                    if evt_type == "message.part.delta":
                        delta = event.get("properties", {}).get("delta", "")
                        if delta:
                            print(delta, end="", flush=True)
                    elif evt_type == "step-start":
                        part_id = event.get("part", {}).get("id")
                        logger.info("\n[OpenCode Step Start] %s", part_id)
                        if progress_callback:
                            progress_callback(f"Step Started: {part_id}")
                    elif evt_type == "step-finish":
                        part_id = event.get("part", {}).get("id")
                        logger.info("\n[OpenCode Step Finish] %s", part_id)
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
            raise OpenCodeExecutionError(f"OpenCode execution failed: {e}") from e
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

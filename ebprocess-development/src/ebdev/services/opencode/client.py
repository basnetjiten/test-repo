# -*- coding: utf-8 -*-
"""
client.py
=========
Client wrapper for communication with the headless OpenCode deepagent server.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import TYPE_CHECKING, Any, AsyncGenerator
from urllib.parse import quote

import httpx

from ebdev.config import config
from ebdev.core.constants import RegexPatterns
from ebdev.core.logger import get_logger
from ebdev.core.throttle import CircuitBreaker, RateLimiter

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fallback OpenCode server URL if not configured in environmental settings.
DEFAULT_OPENCODE_SERVER_URL: str = config.OPENCODE_SERVER_DEFAULT_URL


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


def extract_json_block(text: str, task_id: str) -> dict | None:
    """
    Parse the final message text to extract the agent's JobResult JSON structure.

    Parameters
    ----------
    text : str
        The raw reply text from the agent.
    task_id : str
        The job or ticket ID to match within the JSON.

    Returns
    -------
    dict | None
        Parsed result dictionary if successfully extracted and validated, or None.
    """
    if not text:
        return None

    candidate = text.strip()

    # Strategy 0: Accept a raw JSON object response first.
    try:
        data = json.loads(candidate)
        if isinstance(data, dict) and (
            data.get("task_id") == task_id
            or data.get("ticket_id") == task_id
            or data.get("jira_id") == task_id
            or data.get("job_id") == task_id
        ):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 1: Look for standard markdown ```json blocks
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, dict) and (
                data.get("task_id") == task_id
                or data.get("ticket_id") == task_id
                or data.get("jira_id") == task_id
                or data.get("job_id") == task_id
            ):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 2: Fallback to scanning for raw outer-braces { ... }
    try:
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            data = json.loads(text[start_idx : end_idx + 1])
            if isinstance(data, dict) and (
                data.get("task_id") == task_id
                or data.get("ticket_id") == task_id
                or data.get("jira_id") == task_id
                or data.get("job_id") == task_id
            ):
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

        # Shared HTTP client (reused across all requests)
        self._client = httpx.AsyncClient(timeout=None)

        # Concurrency throttling
        self._rate_limiter = RateLimiter(
            name="opencode",
            max_concurrent=config.RATE_LIMIT_MAX_CONCURRENT,
            max_per_second=config.RATE_LIMIT_PER_SECOND,
        )

        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            name="opencode",
            failure_threshold=config.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            recovery_timeout=config.CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
        )

    async def close(self) -> None:
        """Release the shared HTTP client."""
        await self._client.aclose()

    def _params(self) -> dict[str, str] | None:
        """Return request query parameters for the configured project directory."""
        if not self.directory:
            return None
        return {"directory": self.directory}

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send an HTTP request wrapped with rate limiting and circuit breaker."""
        async with self._rate_limiter, self._circuit_breaker:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

    async def check_health(self) -> dict:
        """
        Fetch endpoint connectivity and engine verification health status.

        Returns
        -------
        dict
            Health check JSON data.
        """
        res = await self._request("GET", f"{self.base_url}/global/health", headers=self.headers, params=self._params())
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
        payload = {"title": title} if title else {}
        headers = {**self.headers}
        if self.directory:
            headers["x-opencode-directory"] = quote(self.directory, safe="")
        res = await self._request(
            "POST", f"{self.base_url}/session", json=payload, headers=headers, params=self._params()
        )
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

        res = await self._request(
            "POST",
            f"{self.base_url}/session/{session_id}/message",
            json=payload,
            headers=self.headers,
            params=self._params(),
        )
        return res.json()

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream real-time server-sent events (SSE).

        Yields
        ------
        dict[str, Any]
            The parsed SSE event payload.
        """
        async with self._client.stream(
            "GET",
            f"{self.base_url}/event",
            headers=self.headers,
            params=self._params(),
        ) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data:"):
                    with contextlib.suppress(json.JSONDecodeError):
                        yield json.loads(line[5:])

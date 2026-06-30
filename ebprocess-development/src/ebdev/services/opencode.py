# -*- coding: utf-8 -*-
"""OpenCode CLI runner and execution parser for ebprocess-development."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import typing
from pathlib import Path

from pydantic import ValidationError

from ebdev.config import config
from ebdev.core.constants import Agents, Commands, ErrorMessages, RegexPatterns
from ebdev.core.logger import get_logger
from ebdev.models.schemas import JobContext, JobResult
from ebdev.services import prompts

logger = get_logger(__name__)


def extract_figma_url(description: str) -> str | None:
    """Return the first Figma design URL found in the description, or None."""
    if not description:
        return None
    match = re.search(RegexPatterns.FIGMA_URL, description)
    if match:
        return match.group(0)
    return None


class OpenCodeService:
    """Service for interacting with the OpenCode CLI."""

    @staticmethod
    def _extract_json(text: str, job_id: str) -> dict | None:
        """Extract the agent's result JSON from OpenCode output."""
        for line in reversed(text.splitlines()):
            raw = re.sub(RegexPatterns.OPENCODE_PREFIX, "", line.strip())
            if '"type":"text"' not in raw and '"type": "text"' not in raw:
                continue
            try:
                event = json.loads(raw)
                inner = event.get("part", {}).get("text", "")
            except (json.JSONDecodeError, TypeError):
                continue

            # Try to find JSON in markdown blocks first
            match = re.search(RegexPatterns.JSON_BLOCK, inner, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, dict) and (data.get("job_id") == job_id or data.get("jira_id") == job_id):
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass

            # Fallback: Try to find and parse raw JSON object
            try:
                start = inner.find('{')
                end = inner.rfind('}')
                if start != -1 and end != -1 and end > start:
                    potential_json = inner[start:end+1]
                    data = json.loads(potential_json)
                    if isinstance(data, dict) and (data.get("job_id") == job_id or data.get("jira_id") == job_id):
                        logger.info(f"Successfully extracted raw JSON for job {job_id}")
                        return data
            except (json.JSONDecodeError, TypeError):
                pass

            continue

        return None

    @staticmethod
    def write_context(job_context: JobContext) -> Path:
        """Serialise JobContext to context.json in the standardized tasks directory."""
        if job_context.jira_ticket.figma_url is None:
            url = extract_figma_url(job_context.jira_ticket.description)
            if url:
                job_context.jira_ticket.figma_url = url

        global_tasks_dir = Path(config.OPENCODE_PROJECT_DIR) / "tasks"
        global_tasks_dir.mkdir(parents=True, exist_ok=True)

        ctx_path = global_tasks_dir / "context.json"
        ctx_path.write_text(job_context.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

        return ctx_path

    @staticmethod
    def _resolve_agent(job_context: JobContext) -> str:
        """Resolve agnostic phase names to platform-specific agent names."""
        phase_map = {
            "flutter": {
                "plan": Agents.FLUTTER_PLANNER,
                "build": Agents.FLUTTER_BUILDER,
            },
            "api": {
                "plan": "api_planner",
                "build": "api_builder",
            },
            "web": {
                "plan": "web_planner",
                "build": "web_builder",
            },
            "cms": {
                "plan": "cms_planner",
                "build": "cms_builder",
            }
        }
        return phase_map.get(job_context.platform, {}).get(
            job_context.current_agent, job_context.current_agent
        )

    @staticmethod
    def _stream_popen(
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
        timeout: int,
        progress_callback: typing.Callable[[str], None] | None,
        job_id: str,
    ) -> tuple[int, str, str | None]:
        """Run a command via Popen and stream stdout line-by-line."""
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
            universal_newlines=True,
        )
        full_output: list[str] = []
        captured_session_id: str | None = None
        start_time = time.time()

        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    if time.time() - start_time > timeout:
                        proc.kill()
                        raise TimeoutError(ErrorMessages.TIMEOUT.format(timeout=timeout))
                    time.sleep(0.1)
                    continue

                logger.info(f"[opencode] {line.strip()}")
                full_output.append(line)

                # Capture session ID from early JSON events
                if not captured_session_id and '"sessionID"' in line:
                    try:
                        json_str = line.split("] ", 1)[-1] if "] " in line else line
                        event = json.loads(json_str)
                        sid = event.get("sessionID") or event.get("session_id")
                        if sid:
                            captured_session_id = sid
                            logger.info(f"Captured session ID: {captured_session_id}")
                    except (json.JSONDecodeError, KeyError):
                        pass

                # Progress callbacks and early termination from structured JSON events
                line_json = OpenCodeService._extract_json(line, job_id)
                if line_json:
                    if progress_callback:
                        progress_callback(f"Agent Status: {line_json.get('status', 'running')}")
                    logger.info("Final JSON result received. Terminating agent process early to prevent interactive hang.")
                    proc.terminate()
                elif progress_callback and '{"type":' in line:
                    try:
                        json_str = line.split("] ", 1)[-1] if "] " in line else line
                        event = json.loads(json_str)
                        if event.get("type") == "step_finish":
                            progress_callback(
                                f"Completed: {event.get('part', {}).get('id', 'step')}"
                            )
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

            proc.wait()
        except Exception as e:
            proc.kill()
            logger.error(f"Execution failed: {e}")
            raise

        return proc.returncode, "".join(full_output), captured_session_id

    @classmethod
    def invoke(
        cls,
        job_context: JobContext,
        progress_callback: typing.Callable[[str], None] | None = None,
        session_id: str | None = None,
    ) -> tuple[JobResult, str | None]:
        """Invoke the OpenCode CLI with the prepared context file."""
        agent = cls._resolve_agent(job_context)
        storage_dir = Path(config.OPENCODE_PROJECT_DIR)
        
        # Ensure standard directory structure exists
        (storage_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (storage_dir / "plans").mkdir(parents=True, exist_ok=True)
        
        ctx_path = cls.write_context(job_context)
        repo_path = Path(job_context.repo_path)

        cmd = [
            config.OPENCODE_BIN,
            Commands.RUN,
            Commands.AGENT_FLAG, agent,
            Commands.DIR_FLAG, str(repo_path.absolute()),
            Commands.FILE_FLAG, str(ctx_path.absolute()),
            Commands.FORMAT_FLAG, Commands.JSON_FORMAT,
            Commands.PRINT_LOGS_FLAG,
        ]

        if config.OPENCODE_MODEL:
            cmd.extend([Commands.MODEL_FLAG, config.OPENCODE_MODEL])

        cmd.append(prompts.build_prompt(job_context, storage_dir=storage_dir, session_id=session_id))

        env_extra: dict[str, str] = {
            k: v for k, v in {
                "JOB_ID": job_context.jira_ticket_id,
                "OPENCODE_PROJECT_DIR": config.OPENCODE_PROJECT_DIR,
                "WORKSPACE_DIR": config.WORKSPACE_DIR,
                "OPENCODE_API_KEY": config.OPENCODE_API_KEY,
                "OPENCODE_MODEL": config.OPENCODE_MODEL,
                "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY,
                "OPENAI_API_KEY": config.OPENAI_API_KEY,
                "GOOGLE_GENERATIVE_AI_API_KEY": config.GOOGLE_GENERATIVE_AI_API_KEY,
                "GROQ_API_KEY": config.GROQ_API_KEY,
            }.items() if v
        }

        returncode, stdout_str, captured_session_id = cls._stream_popen(
            cmd=cmd,
            cwd=str(repo_path.absolute()),
            env={**os.environ, **env_extra},
            timeout=600,
            progress_callback=progress_callback,
            job_id=job_context.jira_ticket_id,
        )

        logger.info(f"Agent {agent} COMPLETED with return code {returncode}")

        data = cls._extract_json(stdout_str, job_context.jira_ticket_id)

        # CHECK FOR HIDDEN ERRORS (AuthError, Tool Crashes)
        is_auth_error = (
            "Invalid API key" in stdout_str or 
            ("[opencode] ERROR" in stdout_str and "Auth" in stdout_str)
        )
        if is_auth_error:
            logger.error("Status: FAILED (Authentication Error)")
            return JobResult(
                job_id=job_context.jira_ticket_id,
                jira_space_name=job_context.jira_space_name,
                jira_id=job_context.jira_ticket_id,
                status="failed",
                errors=[ErrorMessages.AUTH_ERROR],
            ), captured_session_id

        # If we got valid data, treat it as success even if returncode != 0
        if data:
            try:
                logger.info("Status: SUCCESS")
                if "jira_space_name" not in data:
                    data["jira_space_name"] = job_context.jira_space_name
                if "jira_id" not in data:
                    data["jira_id"] = job_context.jira_ticket_id
                if "job_id" not in data:
                    data["job_id"] = job_context.jira_ticket_id
                if "status" not in data:
                    data["status"] = "success"

                return JobResult(**data), captured_session_id
            except (ValueError, ValidationError) as e:
                logger.warning(f"Validation of result JSON failed: {e}")

        # If no valid data was found, check returncode
        if returncode != 0:
            error_msg = ErrorMessages.EXIT_ERROR.format(returncode=returncode)
            logger.error(f"Status: FAILED ({error_msg})")
            return JobResult(
                job_id=job_context.jira_ticket_id,
                jira_space_name=job_context.jira_space_name,
                jira_id=job_context.jira_ticket_id,
                status="failed",
                errors=[error_msg, stdout_str[:2000]],
            ), captured_session_id

        if job_context.current_agent in (Agents.FLUTTER_BUILDER, Agents.FLUTTER_PLANNER):
            if not data:
                logger.error(f"Status: FAILED (No JSON result from {job_context.current_agent})")
                return JobResult(
                    job_id=job_context.jira_ticket_id,
                    jira_space_name=job_context.jira_space_name,
                    jira_id=job_context.jira_ticket_id,
                    status="failed",
                    errors=[ErrorMessages.NO_JSON_RESULT],
                ), captured_session_id

        logger.warning("Status: SUCCESS (WARNING: No JSON result found inside OpenCode output)")
        return JobResult(
            job_id=job_context.jira_ticket_id,
            jira_space_name=job_context.jira_space_name,
            jira_id=job_context.jira_ticket_id,
            status="success",
            summary="Operation completed (no JSON summary returned by agent).",
        ), captured_session_id


def invoke_opencode(*args, **kwargs):
    """Wrapper for backward compatibility."""
    return OpenCodeService.invoke(*args, **kwargs)


def write_context(*args, **kwargs):
    """Wrapper for backward compatibility."""
    return OpenCodeService.write_context(*args, **kwargs)

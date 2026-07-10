import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, model_validator

from ebdev.config import config
from ebdev.core.exceptions import EbDevError, GitServiceError, OpenCodeExecutionError, UnsupportedPlatformError
from ebdev.core.graph import graph
from ebdev.core.logger import get_logger, setup_logging
from ebdev.core.telemetry import instrument_fastapi, setup_telemetry
from ebdev.models.graph_state import GraphState, JobContext
from ebdev.models.spoq import SPOQMapEpic
from ebdev.models.ticket import EpicTask, SprintTicket
from ebdev.services.db import check_db, close_db, init_db
from ebdev.services.epic_state import get_epic_state_service
from ebdev.services.fs import AsyncFileSystemService
from ebdev.services.git import GitConflictError

setup_logging()
setup_telemetry()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup
    try:
        await init_db()
    except Exception as e:
        logger.error("Error during startup database initialization: %s", e)
    yield
    # Shutdown — clean up resources
    try:
        await close_db()
    except Exception as e:
        logger.warning("Error closing database connections: %s", e)
    # Close the graph checkpointer if it has a close method
    try:
        checkpointer = getattr(graph, "checkpointer", None)
        if checkpointer is not None and hasattr(checkpointer, "aclose"):
            await checkpointer.aclose()
    except Exception as e:
        logger.warning("Error closing graph checkpointer: %s", e)


app = FastAPI(title="EBProcess Pipeline API", lifespan=lifespan)
instrument_fastapi(app)


# ---------------------------------------------------------------------------
# Global exception handlers — domain errors → consistent JSON responses
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(UnsupportedPlatformError)
async def unsupported_platform_handler(_request: Request, exc: UnsupportedPlatformError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc), "error_type": "unsupported_platform"})


@app.exception_handler(GitConflictError)
async def git_conflict_handler(_request: Request, exc: GitConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "error_type": "git_conflict"})


@app.exception_handler(OpenCodeExecutionError)
async def opencode_error_handler(_request: Request, exc: OpenCodeExecutionError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc), "error_type": "opencode_error"})


@app.exception_handler(GitServiceError)
async def git_service_handler(_request: Request, exc: GitServiceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc), "error_type": "git_service"})


@app.exception_handler(EbDevError)
async def ebdev_error_handler(_request: Request, exc: EbDevError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc), "error_type": "ebdev_error"})


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_type": "internal"})


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint returning API and dependency status."""
    status = {"status": "ok", "version": "0.1.0", "checks": {}}

    # Check database connectivity
    try:
        db_ok = await check_db()
        status["checks"]["database"] = "ok" if db_ok else "degraded"
        if not db_ok:
            status["status"] = "degraded"
    except Exception as e:
        status["checks"]["database"] = f"error: {e}"
        status["status"] = "degraded"

    return status


class ExecutePipelineRequest(BaseModel):
    """
    Request model for triggering a pipeline execution for a specific sprint ticket.

    When ``resume`` is ``True`` (default) and a checkpoint exists for the
    ``ticket_id`` thread, the pipeline resumes from the last successful node
    rather than starting over. Set to ``False`` to clear history and start fresh.
    """

    id: int
    space_name: str
    ticket_id: str
    title: str
    description: str
    tasks: List[EpicTask]
    platforms: List[str] = ["flutter", "api"]
    project_repo: str = config.DEFAULT_PROJECT_REPO
    starter_types: Dict[str, str] = {"api": "nestjs", "flutter": "flutter"}
    map_id: str | None = None
    epics: List[SPOQMapEpic] = Field(default_factory=list)
    resume: bool = True
    rework_failed_tasks: bool = False

    @model_validator(mode="before")
    @classmethod
    def pre_validate(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Check for GraphQL nested data
            planning = None
            if "data" in data and isinstance(data["data"], dict) and "getProjectPlanning" in data["data"]:
                planning = data["data"]["getProjectPlanning"]
            elif "getProjectPlanning" in data and isinstance(data["getProjectPlanning"], dict):
                planning = data["getProjectPlanning"]

            if planning:
                logger.info("Converting raw GraphQL planning payload to ExecutePipelineRequest")
                raw_epics = planning.get("epics") or []
                converted_epics = []
                all_platforms = set()
                all_tasks = []

                for epic in raw_epics:
                    epic_id = str(epic.get("id"))
                    if not epic_id.startswith("Epic-"):
                        epic_id = f"Epic-{epic_id}"

                    epic_tasks = []
                    for t in epic.get("tasks") or []:
                        t_id = t.get("id")
                        t_name = t.get("name", "")
                        t_status = t.get("status", "todo")

                        # Normalize status to lowercase alphanumeric with underscores
                        t_status_norm = t_status.lower().replace(" ", "_")

                        plat_data = t.get("platform") or {}
                        plat_id = plat_data.get("id")
                        plat_name = plat_data.get("name", "")

                        # Estimated hours
                        est_hour = float(t.get("originalEstimateHour") or 0.0)

                        # Track all platforms
                        normalized_plat = plat_name.lower()
                        if normalized_plat == "web app":
                            normalized_plat = "web"
                        if normalized_plat:
                            all_platforms.add(normalized_plat)

                        task_hour = {
                            "estimatedHour": est_hour,
                            "taskId": t_id,
                            "platformId": plat_id,
                            "platform": {
                                "id": plat_id,
                                "name": plat_name
                            }
                        }

                        epic_tasks.append({
                            "id": t_id,
                            "name": t_name,
                            "status": t_status_norm,
                            "platform": {
                                "id": plat_id,
                                "name": plat_name
                            } if plat_name else None,
                            "platformId": plat_id,
                            "hours": [task_hour]
                        })

                    converted_epics.append({
                        "id": epic_id,
                        "title": epic.get("name", "Unnamed Epic"),
                        "description": epic.get("name", "Unnamed Epic"),
                        "status": "planned",
                        "sprint": "sprint-1",
                        "depends_on": [],
                        "platforms": list(dict.fromkeys([
                            "web" if p.lower() == "web app" else p.lower()
                            for p in [t.get("platform", {}).get("name", "") for t in epic.get("tasks") or []]
                            if p
                        ])),
                        "tasks": epic_tasks,
                        "acceptance_criteria": [],
                        "estimated_hours": 0.0
                    })
                    all_tasks.extend(epic_tasks)

                # Set request level fields
                first_epic = converted_epics[0] if converted_epics else {}
                plan_name = planning.get("name") or first_epic.get("title") or "project_workspace"
                slugified_name = re.sub(r'[\W_]+', '-', str(plan_name).lower()).strip('-')
                default_space = os.environ.get("SPACE_NAME") or data.get("space_name") or slugified_name

                data["id"] = data.get("id") or planning.get("id") or 918
                data["space_name"] = default_space
                data["ticket_id"] = data.get("ticket_id") or first_epic.get("id") or f"Epic-{data['id']}"
                data["title"] = data.get("title") or first_epic.get("title") or planning.get("name", "Project Planning")
                data["description"] = data.get("description") or first_epic.get("description") or planning.get("name", "Project Planning")
                data["tasks"] = all_tasks
                data["platforms"] = list(all_platforms) if all_platforms else ["flutter", "api"]
                data["epics"] = converted_epics

                if "project_repo" not in data:
                    data["project_repo"] = config.DEFAULT_PROJECT_REPO

                if "starter_types" not in data:
                    starter_map = {}
                    for p in all_platforms:
                        if p == "api":
                            starter_map["api"] = "nestjs"
                        elif p == "flutter":
                            starter_map["flutter"] = "flutter"
                        elif p == "web":
                            starter_map["web"] = "nextjs"
                        elif p == "cms":
                            starter_map["cms"] = "react"
                    data["starter_types"] = starter_map

                return data

        return data


MAX_RETRIES = config.MAX_RETRIES
RETRY_BACKOFF_BASE = config.RETRY_BACKOFF_BASE


async def _invoke_with_retry(graph: Any, initial_state: GraphState | None, run_config: RunnableConfig) -> Dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return await graph.ainvoke(initial_state, config=run_config, durability="async")
        except Exception as exc:
            if _is_transient_db_error(exc):
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BACKOFF_BASE**attempt
                    logger.warning(
                        "Transient DB error on attempt %d/%d, retrying in %ds: %s", attempt + 1, MAX_RETRIES, delay, exc
                    )
                    await asyncio.sleep(delay)
                    continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Retry loop completed without execution or error")


def _is_transient_db_error(exc: Exception) -> bool:
    try:
        import psycopg  # noqa: PLC0415

        if isinstance(exc, psycopg.OperationalError):
            return True
    except ImportError:
        pass
    cause = exc
    while cause is not None:
        name = type(cause).__qualname__
        if name in ("AdminShutdown", "OperationalError", "InterfaceError"):
            return True
        cause = cause.__cause__
    return False


@app.post("/execute")
async def execute_pipeline(request: ExecutePipelineRequest):
    """
    Executes the LangGraph pipeline synchronously.

    Uses LangGraph checkpointing for fault-tolerant resume: when a checkpoint
    exists for the thread and ``resume`` is ``True``, execution picks up from
    the last successful node instead of starting from scratch.
    """

    ticket = SprintTicket(
        id=request.ticket_id, title=request.title, description=request.description, status="todo", tasks=request.tasks
    )

    context = JobContext(
        task_id=str(request.id),
        space_name=request.space_name,
        ticket_id=request.ticket_id,
        ticket=ticket,
        repo_path="",
        project_repo=request.project_repo,
        platforms=request.platforms,
        current_agent="plan",
        starter_types=request.starter_types,
        map_id=request.map_id,
        map_epics=request.epics,
    )

    initial_state = GraphState(context=context, done=False, failed=False)
    run_config: RunnableConfig = {"configurable": {"thread_id": f"thread-{request.ticket_id}"}}

    try:
        # ------------------------------------------------------------------
        # Rework Failed Tasks Logic
        # ------------------------------------------------------------------
        if request.rework_failed_tasks:
            # Resolve the in-repo epic directory based on space_name
            epic_dir = Path(config.WORKSPACE_DIR) / request.space_name / ".ebpearls" / request.ticket_id

            svc = get_epic_state_service(epic_dir)
            snapshot = await svc.load()
            if snapshot:
                updated_tasks = False
                for task_id, task in snapshot.tasks.items():
                    if task.status in ("needs_review", "failed"):
                        logger.info("Rework requested: Task %s changed from %s to building.", task_id, task.status)
                        task.status = "building"
                        task.repair_iteration = 0
                        updated_tasks = True
                if updated_tasks:
                    await svc.save(snapshot)

            # Force LangGraph to wipe the dead thread so it starts fresh at preflight
            request.resume = False

        # ------------------------------------------------------------------
        # Clear stale state.json if running fresh (resume = False)
        # ------------------------------------------------------------------
        if not request.resume and not request.rework_failed_tasks:
            epic_dir = Path(config.WORKSPACE_DIR) / request.space_name / ".ebpearls" / request.ticket_id
            state_json_path = epic_dir / "state.json"
            try:
                if await AsyncFileSystemService.delete_file(state_json_path):
                    logger.info("Successfully deleted stale state.json at %s", state_json_path)
            except Exception as exc:
                logger.warning("Failed to delete stale state.json at %s: %s", state_json_path, exc)

        # ------------------------------------------------------------------
        # Checkpoint-aware resume logic
        # ------------------------------------------------------------------
        # graph.aget_state() returns a StateSnapshot if a checkpoint exists
        # for this thread. If ``next`` is non-empty, the graph was interrupted
        # or failed mid-execution and can be resumed.
        existing_state = None
        try:
            existing_state = await graph.aget_state(run_config)
        except Exception as exc:
            logger.warning(
                "Failed to retrieve checkpoint state for %s: %s. Starting fresh.",
                request.ticket_id,
                exc,
                exc_info=True,
            )

        was_resumed = False

        if existing_state is not None:
            if not request.resume:
                logger.info("Resume disabled — clearing checkpoint history for %s.", request.ticket_id)
                checkpointer = getattr(graph, "checkpointer", None)
                if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
                    try:
                        await checkpointer.adelete_thread(f"thread-{request.ticket_id}")
                    except Exception as exc:
                        logger.warning(
                            "Failed to clear checkpoints for %s: %s; proceeding with fresh state.",
                            request.ticket_id,
                            exc,
                            exc_info=True,
                        )
                final_state = await _invoke_with_retry(graph, initial_state, run_config)
            elif existing_state.next:
                logger.info("Resuming pipeline from checkpoint for %s (pending: %s)", request.ticket_id, existing_state.next)
                final_state = await _invoke_with_retry(graph, None, run_config)
                was_resumed = True
            else:
                logger.info("Pipeline already finished for %s. Returning existing final state.", request.ticket_id)
                final_state = await _invoke_with_retry(graph, initial_state, run_config)
        else:
            logger.info("Starting fresh pipeline for %s.", request.ticket_id)
            final_state = await _invoke_with_retry(graph, initial_state, run_config)
        
        result = final_state.get("result")
        if result:
            return {
                "status": "success",
                "resumed": was_resumed,
                "done": final_state.get("done"),
                "failed": final_state.get("failed"),
                "last_node": final_state.get("last_node"),
                "result": result.model_dump(),
            }
        return {
            "status": "partial",
            "resumed": was_resumed,
            "done": final_state.get("done"),
            "failed": final_state.get("failed"),
            "last_node": final_state.get("last_node"),
            "message": "No result found in final state.",
        }
    except Exception as exc:
        logger.exception("Pipeline execution failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

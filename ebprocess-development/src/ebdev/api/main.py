import asyncio
import logging
import sys
import uuid
from typing import Dict, List

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ebdev.core.graph import graph
from ebdev.models.schemas import (
    EpicTask,
    GraphState,
    JobContext,
    SPOQMapEpic,
    SprintTicket,
)

# Configure root logging to output all logs to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Postgres database tables
    try:
        from ebdev.services.db import init_db
        await init_db()
    except Exception as e:
        logger.error("Error during startup database initialization: %s", e)
    yield

app = FastAPI(title="EBProcess Pipeline API", lifespan=lifespan)

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
    project_repo: str = "https://bitbucket.org/basnetjiten7/test-repo.git"
    starter_types: Dict[str, str] = {"api": "nestjs", "flutter": "flutter"}
    map_id: str | None = None
    epics: List[SPOQMapEpic] = Field(default_factory=list)
    resume: bool = True

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2

async def _invoke_with_retry(graph, initial_state, config):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return await graph.ainvoke(initial_state, config=config, durability="async")
        except Exception as exc:
            if _is_transient_db_error(exc):
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(
                        "Transient DB error on attempt %d/%d, retrying in %ds: %s",
                        attempt + 1, MAX_RETRIES, delay, exc
                    )
                    await asyncio.sleep(delay)
                    continue
            raise
    raise last_exc

def _is_transient_db_error(exc: Exception) -> bool:
    try:
        import psycopg
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
        id=request.ticket_id,
        title=request.title,
        description=request.description,
        status="todo",
        assignee="api-agent",
        tasks=request.tasks
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
    config = {"configurable": {"thread_id": f"thread-{request.ticket_id}"}}

    try:
        # ------------------------------------------------------------------
        # Checkpoint-aware resume logic
        # ------------------------------------------------------------------
        # graph.aget_state() returns a StateSnapshot if a checkpoint exists
        # for this thread. If ``next`` is non-empty, the graph was interrupted
        # or failed mid-execution and can be resumed.
        existing_state = None
        try:
            existing_state = await graph.aget_state(config)
        except Exception:
            logger.debug("No existing checkpoint for %s — starting fresh.", request.ticket_id)

        was_resumed = False

        if existing_state is not None and existing_state.next:
            pending = existing_state.next
            if request.resume:
                logger.info(
                    "Resuming pipeline from checkpoint for %s (pending: %s)",
                    request.ticket_id, pending,
                )
                # None input tells LangGraph to load the last checkpoint state
                final_state = await _invoke_with_retry(graph, None, config)
                was_resumed = True
            else:
                logger.info(
                    "Resume disabled — clearing checkpoint history for %s.",
                    request.ticket_id,
                )
                checkpointer = getattr(graph, "checkpointer", None)
                if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
                    try:
                        await checkpointer.adelete_thread(f"thread-{request.ticket_id}")
                    except Exception:
                        logger.warning(
                            "Failed to clear checkpoints for %s; proceeding with fresh state.",
                            request.ticket_id,
                        )
                final_state = await _invoke_with_retry(graph, initial_state, config)
        else:
            logger.info("Starting fresh pipeline for %s.", request.ticket_id)
            final_state = await _invoke_with_retry(graph, initial_state, config)
        
        result = final_state.get("result")
        if result:
            return {
                "status": "success",
                "resumed": was_resumed,
                "done": final_state.get("done"),
                "failed": final_state.get("failed"),
                "last_node": final_state.get("last_node"),
                "result": result.model_dump()
            }
        else:
            return {
                "status": "partial",
                "resumed": was_resumed,
                "done": final_state.get("done"),
                "failed": final_state.get("failed"),
                "last_node": final_state.get("last_node"),
                "message": "No result found in final state."
            }
    except Exception as exc:
        logger.exception("Pipeline execution failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

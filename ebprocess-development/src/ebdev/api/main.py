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

@app.post("/execute")
async def execute_pipeline(request: ExecutePipelineRequest):
    """
    Executes the LangGraph pipeline synchronously. 
    It will block until the pipeline completes and returns the final job result.
    """
    
    ticket = SprintTicket(
        id=request.ticket_id,
        title=request.title,
        description=request.description,
        status="todo",
        assignee="api-agent",
        tasks=request.tasks
    )

    # Initialize job context similar to the test script
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

    

    try:
        logger.info("Invoking pipeline for %s", request.ticket_id)
        config = {"configurable": {"thread_id": f"thread-{request.ticket_id}"}}
        final_state = await graph.ainvoke(initial_state, config=config)
        
        result = final_state.get("result")
        if result:
            return {
                "status": "success",
                "done": final_state.get("done"),
                "failed": final_state.get("failed"),
                "last_node": final_state.get("last_node"),
                "result": result.model_dump()
            }
        else:
            return {
                "status": "partial",
                "done": final_state.get("done"),
                "failed": final_state.get("failed"),
                "last_node": final_state.get("last_node"),
                "message": "No result found in final state."
            }
    except Exception as exc:
        logger.exception("Pipeline execution failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

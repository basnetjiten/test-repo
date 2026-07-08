# -*- coding: utf-8 -*-
"""
graph.py
========
LangGraph orchestrator graph definition for ebprocess-development.

Responsibilities
---------------
* Define routing conditions between different pipeline stages.
* Construct and compile the stateful execution graph (LangGraph StateGraph).
* Provide checkpointing via AsyncPostgresSaver for production persistence,
  with graceful fallback to MemorySaver for development or when Postgres
  is unreachable.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import sys
from typing import TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
import psycopg
from psycopg_pool import AsyncConnectionPool

from ebdev.config import config
from ebdev.core.nodes import (
    contract_node,
    finalize_node,
    generate_node,
    orchestrate_node,
    plan_node,
    preflight_node,
    prepare_node,
    publish_node,
    repair_node,
    validate_node,
)
from ebdev.models.schemas import GraphState

if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Sequence
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import (
        ChannelVersions,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
    )
    from langgraph.graph.state import CompiledStateGraph

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Checkpointing strategy
# ---------------------------------------------------------------------------
# PostgresSaver persists GraphState after every node transition so that
# sessions survive restarts and can resume from the exact last node.
# Falls back to MemorySaver (in-memory) when POSTGRES_URL is unset or Postgres
# is unreachable.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Resilient Postgres wrapper — catches transient connection errors (e.g.
# AdminShutdown after a server restart) and retries with exponential backoff.
# ---------------------------------------------------------------------------
class ResilientPostgresSaver(BaseCheckpointSaver):
    """
    Retry-wrapping proxy around ``AsyncPostgresSaver``.

    Proxies the full ``BaseCheckpointSaver`` contract with exponential
    backoff on transient ``psycopg.OperationalError`` exceptions:

    * ``aget_tuple`` — load latest or specific checkpoint
    * ``aput`` — persist a checkpoint at a super-step boundary
    * ``aput_writes`` — persist intermediate node writes for fault tolerance
    * ``alist`` — list checkpoint history for a thread
    * ``adelete_thread`` — delete all checkpoints + writes for a thread
    * ``asetup`` — create tables if needed
    """

    _saver: AsyncPostgresSaver | None
    _pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]
    _max_retries: int

    def __init__(
        self,
        pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]],
        max_retries: int = 3,
    ) -> None:
        super().__init__()
        self._saver = None
        self._pool = pool
        self._max_retries = max_retries

    async def _get_saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            self._saver = AsyncPostgresSaver(self._pool)
        return self._saver

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                return await saver.aget_tuple(config)
            except psycopg.OperationalError:
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                return await saver.aput(config, checkpoint, metadata, new_versions)
            except psycopg.OperationalError:
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("Unreachable: retry loop exhausted")

    async def asetup(self) -> None:
        saver = await self._get_saver()
        await saver.setup()

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                async for checkpoint_tuple in saver.alist(
                    config, filter=filter, before=before, limit=limit
                ):
                    yield checkpoint_tuple
                return
            except psycopg.OperationalError:
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                return await saver.aput_writes(config, writes, task_id, task_path)
            except psycopg.OperationalError:
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def adelete_thread(self, thread_id: str) -> None:
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                return await saver.adelete_thread(thread_id)
            except psycopg.OperationalError:
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)


# ---------------------------------------------------------------------------
# Routing Logic
# ---------------------------------------------------------------------------
def _route_after_preflight(state: GraphState) -> str:
    """
    Route execution after the preflight state analysis.
    
    If preflight determined that some tasks can skip planning or building,
    jump directly to the target node.
    """
    if state.preflight_skip_to:
        logger.info("Preflight route redirect → %s", state.preflight_skip_to)
        return state.preflight_skip_to
    return "planner_agent"


def _route_after_plan(state: GraphState) -> str:
    """
    Route after the planning phase.

    Routes to builder_agent on success, or repair_agent on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("builder_agent" or "repair_agent").
    """
    if state.result and state.result.status == "failed":
        return "repair_agent"
    return "builder_agent"


def _route_after_generate(state: GraphState) -> str:
    """
    Route after the code generation phase.

    Routes to evaluator_agent on success, or repair_agent on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("evaluator_agent" or "repair_agent").
    """
    if state.result and state.result.status == "failed":
        return "repair_agent"
    return "evaluator_agent"


def _route_after_validate(state: GraphState) -> str:
    """
    Route after the validation phase.

    Routes to next planning stage on success, repair_agent on failure, or contract_agent when finished.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("planner_agent", "repair_agent", "publish_agent", or "contract_agent").

    Raises
    ------
    ValueError
        If the SPOQ epic task directory is missing when execution mode is SPOQ.
    """
    if state.failed or any(state.failed_platforms.values()):
        return "repair_agent"

    if state.is_spoq:
        return "publish_agent" if state.done else "planner_agent"

    # Backward compatibility for parallel/sequential without SPOQ
    active_platforms = state.context.platforms
    has_failures = any(state.failed_platforms.get(p) for p in active_platforms)

    if has_failures:
        return "repair_agent"

    if state.done:
        return "contract_agent"

    return "planner_agent"


def _route_after_contract(state: GraphState) -> str:
    """
    Route after the contract validation phase.

    Routes to publish_agent on success, or repair_agent on failure.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("publish_agent" or "repair_agent").
    """
    if state.result and state.result.status == "failed":
        return "repair_agent"
    return "publish_agent"


def _route_after_repair(state: GraphState) -> str:
    """
    Route after the repair phase.

    Routes to finalize_agent if max retries hit, otherwise back to builder_agent.

    Parameters
    ----------
    state : GraphState
        The current state of the workflow graph.

    Returns
    -------
    str
        The next node name ("finalize_agent" or "builder_agent").
    """
    if state.done:  # max iterations reached (failed or successful)
        return "finalize_agent"
    return "builder_agent"


# ---------------------------------------------------------------------------
# Compile StateGraph
# ---------------------------------------------------------------------------
def build_graph() -> CompiledStateGraph:
    """
    Construct and compile the stateful execution graph.

    Returns
    -------
    CompiledStateGraph
        The compiled LangGraph StateGraph.
    """
    builder = StateGraph(GraphState)

    # Add execution nodes mapped to agent roles
    builder.add_node("prepare", prepare_node)
    builder.add_node("orchestrate_agent", orchestrate_node)
    builder.add_node("preflight_agent", preflight_node)
    builder.add_node("planner_agent", plan_node)
    builder.add_node("builder_agent", generate_node)
    builder.add_node("evaluator_agent", validate_node)
    builder.add_node("contract_agent", contract_node)
    builder.add_node("repair_agent", repair_node)
    builder.add_node("publish_agent", publish_node)
    builder.add_node("finalize_agent", finalize_node)

    # Establish routing edges
    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "orchestrate_agent")
    builder.add_edge("orchestrate_agent", "preflight_agent")
    
    builder.add_conditional_edges("preflight_agent", _route_after_preflight, {
        "planner_agent": "planner_agent",
        "builder_agent": "builder_agent",
        "evaluator_agent": "evaluator_agent",
        "publish_agent": "publish_agent",
        "finalize_agent": "finalize_agent",
    })

    builder.add_conditional_edges("planner_agent", _route_after_plan, {
        "builder_agent": "builder_agent",
        "repair_agent": "repair_agent",
    })

    builder.add_conditional_edges("builder_agent", _route_after_generate, {
        "evaluator_agent": "evaluator_agent",
        "repair_agent": "repair_agent",
    })

    builder.add_conditional_edges("evaluator_agent", _route_after_validate, {
        "contract_agent": "contract_agent",
        "publish_agent": "publish_agent",
        "repair_agent": "repair_agent",
        "planner_agent": "planner_agent",
    })

    builder.add_conditional_edges("contract_agent", _route_after_contract, {
        "publish_agent": "publish_agent",
        "repair_agent": "repair_agent",
    })

    builder.add_conditional_edges("repair_agent", _route_after_repair, {
        "builder_agent": "builder_agent",
        "finalize_agent": "finalize_agent",
    })

    builder.add_edge("publish_agent", "finalize_agent")
    builder.add_edge("finalize_agent", END)

    # Check if running under LangGraph API/dev server
    is_langgraph = (
        "langgraph_api" in sys.modules
        or "langgraph_cli" in sys.modules
        or os.environ.get("LANGGRAPH_API_VERSION") is not None
        or any("langgraph" in arg for arg in sys.argv)
    )

    if is_langgraph:
        if config.POSTGRES_URL:
            try:
                async def _ensure_tables():
                    pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
                        conninfo=config.POSTGRES_URL,
                        min_size=1,
                        max_size=1,
                        max_lifetime=1800,
                        max_idle=600,
                        kwargs={"autocommit": True},
                        open=False,
                    )
                    await pool.open()
                    try:
                        saver = AsyncPostgresSaver(pool)
                        await saver.setup()
                        logger.info(
                            "LangGraph checkpoint tables initialized in Postgres "
                            "(langgraph dev mode — checkpointer managed by server)."
                        )
                    finally:
                        await pool.close()

                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(_ensure_tables())
                else:
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.submit(asyncio.run, _ensure_tables()).result()
            except Exception as e:
                logger.warning(
                    "Failed to initialize Postgres checkpoint tables: %s. "
                    "The LangGraph dev server will use its default backend.",
                    e,
                )
        return builder.compile()
    else:
        if config.POSTGRES_URL:
            try:
                async def _setup():
                    pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
                        conninfo=config.POSTGRES_URL,
                        min_size=1,
                        max_size=10,
                        max_lifetime=1800,
                        max_idle=600,
                        kwargs={"autocommit": True},
                        open=False,
                    )
                    await pool.open()
                    saver = AsyncPostgresSaver(pool)
                    await saver.setup()
                    logger.info("LangGraph graph compiled with AsyncPostgresSaver checkpointer.")
                    return builder.compile(checkpointer=ResilientPostgresSaver(pool))

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop — safe to use asyncio.run() (e.g. test script, langgraph dev)
                    compiled = asyncio.run(_setup())
                else:
                    # Running inside an event loop (e.g. uvicorn reload) — run setup in a
                    # dedicated thread with its own event loop, since AsyncConnectionPool
                    # TCP connections survive across threads/loops.
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        compiled = executor.submit(asyncio.run, _setup()).result()
                return compiled
            except Exception as e:
                logger.warning("Postgres database is unreachable or failed to initialize: %s. Falling back to MemorySaver.", e)
                return builder.compile(checkpointer=MemorySaver())
        else:
            return builder.compile(checkpointer=MemorySaver())



# Module-level compiled graph — required by langgraph.json
graph = build_graph()

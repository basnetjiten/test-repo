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
import contextlib
import os
import sys
from typing import TYPE_CHECKING, Any, Iterator, override

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from psycopg_pool import AsyncConnectionPool

from ebdev.config import config
from ebdev.core.logger import get_logger
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
from ebdev.models.graph_state import GraphState, JobContext, JobResult, OrchestrationStrategy
from ebdev.models.spoq import SPOQTask

allowed_msgpack_modules = [
    JobContext,
    JobResult,
    OrchestrationStrategy,
    SPOQTask
]
custom_serde = JsonPlusSerializer(allowed_msgpack_modules=allowed_msgpack_modules)

if TYPE_CHECKING:
    from typing import AsyncIterator, Sequence

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
logger = get_logger(__name__)

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

    Implements the full ``BaseCheckpointSaver`` contract with exponential
    backoff on transient ``psycopg.OperationalError`` exceptions.

    **Async methods** (primary interface for LangGraph):
    * ``aget_tuple``       — load latest or specific checkpoint
    * ``aput``             — persist a checkpoint at a super-step boundary
    * ``aput_writes``      — persist intermediate node writes
    * ``alist``            — list checkpoint history for a thread
    * ``adelete_thread``   — delete all checkpoints + writes for a thread
    * ``acopy_thread``     — copy checkpoints between threads
    * ``adelete_for_runs`` — delete checkpoints associated with run IDs
    * ``aprune``           — prune old checkpoints by strategy
    * ``asetup``           — create tables if needed

    **Sync methods** (required by BaseCheckpointSaver contract):
    Sync variants delegate to the async equivalents via ``asyncio.run``.
    They are provided for completeness but this class is designed for async
    usage — prefer the ``a``-prefixed methods in all async call sites.
    """

    _saver: AsyncPostgresSaver | None
    _pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]
    _max_retries: int
    _loop: asyncio.AbstractEventLoop | None
    _background_tasks: set[asyncio.Task[Any]]

    def __init__(
        self,
        pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]],
        max_retries: int = 3,
        *,
        serde: Any = None,
    ) -> None:
        super().__init__(serde=serde)
        self._saver = None
        self._pool = pool
        self._max_retries = max_retries
        self._background_tasks = set()
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    async def _get_saver(self) -> AsyncPostgresSaver:
        current_loop = asyncio.get_running_loop()
        if self._saver is None or self._loop is not current_loop:
            if self._loop is not None and self._loop is not current_loop:
                logger.info("Event loop changed. Recreating psycopg connection pool.")
                # Close the old pool in a background task so any CancelledError on the dead loop
                # does not propagate and abort the current request.
                async def _safe_close(old_pool: AsyncConnectionPool[Any]) -> None:
                    with contextlib.suppress(BaseException):
                        await old_pool.close()
                task = asyncio.create_task(_safe_close(self._pool))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                self._pool = AsyncConnectionPool(
                    conninfo=self._pool.conninfo,
                    min_size=self._pool.min_size,
                    max_size=self._pool.max_size,
                    max_lifetime=self._pool.max_lifetime,
                    max_idle=self._pool.max_idle,
                    kwargs=self._pool.kwargs or {},
                    open=False,
                )
            self._loop = current_loop
            if not getattr(self._pool, "_opened", False):
                await self._pool.open()
            self._saver = AsyncPostgresSaver(self._pool, serde=self.serde)
        return self._saver

    async def _retry(self, coro_factory: Any) -> Any:
        """
        Run ``coro_factory()`` with exponential-backoff retry on
        ``psycopg.OperationalError``.  Resets the cached saver on each
        failure so the next attempt obtains a fresh connection.
        """
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                return await coro_factory(saver)
            except psycopg.OperationalError:
                self._saver = None  # force fresh connection on next attempt
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)
        raise RuntimeError("Unreachable")
    # ------------------------------------------------------------------
    # Async interface — primary surface used by LangGraph
    # ------------------------------------------------------------------

    @override
    async def aget_tuple(
        self, config: RunnableConfig
    ) -> CheckpointTuple | None:
        return await self._retry(lambda s: s.aget_tuple(config))

    @override
    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await self._retry(
            lambda s: s.aput(config, checkpoint, metadata, new_versions)
        )

    @override
    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await self._retry(
            lambda s: s.aput_writes(config, writes, task_id, task_path)
        )

    @override
    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:  # type: ignore[override]
        # NOTE: Retry here restarts the generator from the beginning.
        # Callers may receive duplicate rows if a failure occurs mid-stream.
        # This is acceptable for checkpoint history listing (idempotent read).
        for attempt in range(self._max_retries):
            try:
                saver = await self._get_saver()
                async for tup in saver.alist(
                    config, filter=filter, before=before, limit=limit
                ):
                    yield tup
                return
            except psycopg.OperationalError:
                self._saver = None
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)

    @override
    async def adelete_thread(self, thread_id: str) -> None:
        await self._retry(lambda s: s.adelete_thread(thread_id))

    @override
    async def acopy_thread(
        self,
        source_thread_id: str,
        target_thread_id: str,
    ) -> None:
        """Copy all checkpoints and writes from one thread to another.

        Delegates to ``AsyncPostgresSaver.acopy_thread`` with the same
        exponential-backoff retry semantics as all other proxy methods.
        The full parent chain is preserved so ``DeltaChannel`` state can
        be correctly reconstructed on the target thread.
        """
        await self._retry(
            lambda s: s.acopy_thread(source_thread_id, target_thread_id)
        )

    @override
    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        """Delete checkpoints associated with the given run IDs.

        Note: ``AsyncPostgresSaver`` does not yet implement this method.
        Raises ``NotImplementedError`` until the underlying driver adds support.
        """
        raise NotImplementedError(
            "AsyncPostgresSaver does not implement adelete_for_runs."
        )

    @override
    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        """Prune old checkpoints from the given threads by strategy.

        Note: ``AsyncPostgresSaver`` does not yet implement this method.
        Raises ``NotImplementedError`` until the underlying driver adds support.
        """
        raise NotImplementedError(
            "AsyncPostgresSaver does not implement aprune."
        )

    # ------------------------------------------------------------------
    # Sync interface — required by BaseCheckpointSaver contract.
    # Prefer the async (a-prefixed) equivalents at all async call sites.
    # ------------------------------------------------------------------

    @override
    def get_tuple(
        self, config: RunnableConfig
    ) -> CheckpointTuple | None:
        return asyncio.get_event_loop().run_until_complete(self.aget_tuple(config))

    @override
    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        async def _collect() -> list[CheckpointTuple]:
            return [
                tup
                async for tup in self.alist(
                    config, filter=filter, before=before, limit=limit
                )
            ]

        return iter(asyncio.get_event_loop().run_until_complete(_collect()))

    @override
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return asyncio.get_event_loop().run_until_complete(
            self.aput(config, checkpoint, metadata, new_versions)
        )

    @override
    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.aput_writes(config, writes, task_id, task_path)
        )

    @override
    def delete_thread(self, thread_id: str) -> None:
        asyncio.get_event_loop().run_until_complete(self.adelete_thread(thread_id))

    @override
    def copy_thread(
        self,
        source_thread_id: str,
        target_thread_id: str,
    ) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.acopy_thread(source_thread_id, target_thread_id)
        )

    @override
    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        raise NotImplementedError(
            "AsyncPostgresSaver does not implement delete_for_runs."
        )

    @override
    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        raise NotImplementedError(
            "AsyncPostgresSaver does not implement prune."
        )

    # ------------------------------------------------------------------
    # Setup helper (not part of BaseCheckpointSaver contract)
    # ------------------------------------------------------------------

    async def asetup(self) -> None:
        """Create checkpoint tables, with the same retry guarantees as other methods."""
        await self._retry(lambda s: s.setup())


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
    active_platforms = state.context.platforms if state.context else []
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

    builder.add_conditional_edges(
        "preflight_agent",
        _route_after_preflight,
        {
            "planner_agent": "planner_agent",
            "builder_agent": "builder_agent",
            "evaluator_agent": "evaluator_agent",
            "publish_agent": "publish_agent",
            "finalize_agent": "finalize_agent",
        },
    )

    builder.add_conditional_edges(
        "planner_agent",
        _route_after_plan,
        {
            "builder_agent": "builder_agent",
            "repair_agent": "repair_agent",
        },
    )

    builder.add_conditional_edges(
        "builder_agent",
        _route_after_generate,
        {
            "evaluator_agent": "evaluator_agent",
            "repair_agent": "repair_agent",
        },
    )

    builder.add_conditional_edges(
        "evaluator_agent",
        _route_after_validate,
        {
            "contract_agent": "contract_agent",
            "publish_agent": "publish_agent",
            "repair_agent": "repair_agent",
            "planner_agent": "planner_agent",
        },
    )

    builder.add_conditional_edges(
        "contract_agent",
        _route_after_contract,
        {
            "publish_agent": "publish_agent",
            "repair_agent": "repair_agent",
        },
    )

    builder.add_conditional_edges(
        "repair_agent",
        _route_after_repair,
        {
            "builder_agent": "builder_agent",
            "finalize_agent": "finalize_agent",
        },
    )

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
                try:
                    saver = AsyncPostgresSaver(pool, serde=custom_serde)
                    await saver.setup()
                    logger.info("LangGraph graph compiled with AsyncPostgresSaver checkpointer.")
                    return builder.compile(checkpointer=ResilientPostgresSaver(pool, serde=custom_serde))
                except Exception:
                    await pool.close()
                    raise

            try:
                asyncio.get_running_loop()
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
            logger.warning(
                "Postgres database is unreachable or failed to initialize: %s. Falling back to MemorySaver.", e
            )
            return builder.compile(checkpointer=MemorySaver(serde=custom_serde))
    else:
        return builder.compile(checkpointer=MemorySaver(serde=custom_serde))


# Module-level compiled graph — required by langgraph.json
graph = build_graph()

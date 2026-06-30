# Custom Agent Rules for ebprocess-development

These rules govern the architecture, coding practices, and file structures generated for this Python project to ensure it is production-ready, scalable, and built for long-term maintainability.

## 1. Clean Architecture and Domain Boundaries
1. **Core Domain Separation**:
   - Keep domain models and state schemas (`models/`) completely decoupled from execution runners, API routers, and external clients.
   - External CLI tools, subprocess runners, and database connections must be encapsulated in the `services/` layer and conform to clear interfaces.
2. **Strategy Pattern for Platforms**:
   - Multi-platform execution (Flutter, API, Web, CMS) must be handled via a unified Strategy Pattern. Define a base interface `PlatformStrategy` and implement separate classes for each platform.
   - Nodes must call platform strategies rather than hardcoding platform checks (`if platform == "flutter"`).
3. **Decoupled API Clients and Services**:
   - Decouple low-level API client wrappers (which make external HTTP requests or interface with third-party tools) from high-level orchestration/business logic services.
   - The low-level client must handle standard protocols, authorization headers, and raw requests/responses. The orchestrator must handle serialization, prompt construction, and schema validation.

## 2. Concurrent and Non-Blocking Design
1. **Async IO First**:
   - Use Python `asyncio` for non-blocking operations. Ensure node executions running external CLI commands or subprocesses stream outputs asynchronously (using `asyncio.create_subprocess_exec`).
   - Run parallel platforms using `asyncio.gather` to achieve concurrent, multi-platform planning, generation, and validation for JIRA tasks/epics.
2. **Thread Safety**:
   - Fallback loggers, JSON databases, and caching layers must be thread-safe and handle concurrency gracefully when writing task statuses.
3. **Safe Async Tasks Cleanup**:
   - When launching concurrent background tasks (like SSE listeners or event loops), always ensure they are properly cancelled and gathered under a `finally` block to prevent resource leaks or orphaned routines.

## 3. Code Quality, Typing and Python Standards
1. **Pythonic Structure**:
   - Follow standard Python project packaging guidelines (e.g., modular packages, standard imports, docstrings for modules/methods).
   - Use absolute imports within the package (`from ebdev.core import ...` instead of relative `from ..core import ...`).
   - Use `poetry` or standard `pyproject.toml` configurations.
2. **Strong Typing Mandatory**:
   - All functions and class methods MUST have comprehensive type hinting for parameters and return types.
   - Avoid using raw dictionaries (`dict[str, Any]`) for complex data transit; always define Pydantic schemas or standard Python `dataclasses`.
3. **Avoid Dense branching (if-else complexity)**:
   - Minimize nested conditional checks and large branching code logs.
   - Refactor repetitive if-else blocks using lookup dictionaries, strategy patterns, guard clauses (early exits), or polymorphism.
4. **Error Handling & Custom Exceptions**:
   - Never catch broad exceptions (`except Exception:`) without logging and re-raising/translating to domain errors.
   - Define context-specific domain exceptions (e.g. `GitServiceError`, `OpenCodeExecutionError`) to keep code modular and readable.
   - Use Sentry capturing for critical pipeline failures.

## 4. "Dark Factory" Automation Guidelines
1. **Zero-Interaction Headless Executions**:
   - All integrations (git, flutter, pytest, api, opencode) must run fully headlessly and non-interactively (e.g., `--no-interactive` or equivalent flags).
   - Avoid code paths that assume user interaction or wait for stdin inputs.
2. **Robust Telemetry and Log Traceability**:
   - Log steps using structured logging principles. Stream real-time output progress (like SSE event delta prints) to keep logs alive and traceable.
   - Always include unique context variables (such as `job_id`, `jira_id`, `session_id`) in log entries to simplify log correlation.

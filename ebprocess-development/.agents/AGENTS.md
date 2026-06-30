# Custom Agent Rules for ebprocess-development

These rules govern the architecture, coding practices, and file structures generated for this Python project to ensure it is production-ready, scalable, and built for long-term maintainability.

## Clean Architecture and Domain Boundaries
1. **Core Domain Separation**:
   - Keep domain models and state schemas (`models/`) completely decoupled from execution runners, API routers, and external clients.
   - External CLI tools, subprocess runners, and database connections must be encapsulated in the `services/` layer and conform to clear interfaces.
2. **Strategy Pattern for Platforms**:
   - Multi-platform execution (Flutter, API, Web, CMS) must be handled via a unified Strategy Pattern. Define a base interface `PlatformStrategy` and implement separate classes for each platform.
   - Nodes must call platform strategies rather than hardcoding platform checks (`if platform == "flutter"`).

## Concurrent and Non-Blocking Design
1. **Async IO First**:
   - Use Python `asyncio` for non-blocking operations. Ensure node executions running external CLI commands or subprocesses stream outputs asynchronously (using `asyncio.create_subprocess_exec`).
   - Run parallel platforms using `asyncio.gather` to achieve concurrent, multi-platform planning, generation, and validation for JIRA tasks/epics.
2. **Thread Safety**:
   - Fallback loggers, JSON databases, and caching layers must be thread-safe and handle concurrency gracefully when writing task statuses.

## Code Quality and Python Standards
1. **Project Structure**:
   - Follow standard Python project guidelines (e.g., standard imports, type hinting, pydantic schemas, docstrings for modules/methods).
   - Use `poetry` or standard `pyproject.toml` setups.
2. **Error Handling**:
   - Never catch broad exceptions without logging and re-raising/translating to domain errors.
   - Use Sentry capturing for critical pipeline failures.

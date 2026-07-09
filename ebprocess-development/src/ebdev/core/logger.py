from __future__ import annotations

import logging
import os
import sys

import structlog

_DEV_MODE = os.environ.get("EB_DEV", "true").lower() in ("1", "true", "yes")


def setup_logging() -> None:
    """Configure structlog and stdlib logging once at process start."""
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
            ],
        ),
    ]

    if _DEV_MODE:
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True, sort_keys=False),
        ]
    else:
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Silence overly verbose third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Forward structlog warnings/errors to stdlib so any
    # stdlib-integrated tooling (Sentry, etc.) still works.
    stdlib_handler = logging.StreamHandler(sys.stdout)
    stdlib_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(stdlib_handler)
    root.setLevel(logging.INFO if _DEV_MODE else logging.WARNING)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Get a structlog BoundLogger for the given *name*."""
    return structlog.get_logger(name)

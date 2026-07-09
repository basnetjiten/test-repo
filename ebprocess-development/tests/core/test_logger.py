"""Tests for the logging setup and logger utility functions."""

from __future__ import annotations

from ebdev.core.logger import get_logger, setup_logging


class TestLogger:
    """Unit tests for the ebdev.core.logger module and structured logging setup."""

    def setup_method(self) -> None:
        """Initialize the logging system before running each test method."""
        setup_logging()

    def test_get_logger(self) -> None:
        """Test that get_logger returns a structured logger instance with a custom name."""
        log = get_logger("test")
        assert log is not None

    def test_get_logger_default_name(self) -> None:
        """Test that get_logger returns a logger instance with the default package name."""
        log = get_logger()
        assert log is not None

    def test_logger_level_methods(self) -> None:
        """Test logging at different severity levels (debug, info, warning, error)."""
        log = get_logger("test")
        log.debug("debug %s", "msg")
        log.info("info %s", "msg")
        log.warning("warning %s", "msg")
        log.error("error %s", "msg")

    def test_logger_exception(self) -> None:
        """Test that log.exception correctly logs caught exceptions with traceback details."""
        log = get_logger("test")
        try:
            raise ValueError("test exc")
        except ValueError:
            log.exception("caught exception")

    def test_setup_logging_dev_mode(self, monkeypatch) -> None:
        """Test setup_logging configuration when EB_DEV is enabled (development mode)."""
        monkeypatch.setenv("EB_DEV", "true")
        # Should not raise
        setup_logging()
        log = get_logger("test")
        log.info("dev mode test")

    def test_setup_logging_prod_mode(self, monkeypatch) -> None:
        """Test setup_logging configuration when EB_DEV is disabled (production mode)."""
        monkeypatch.setenv("EB_DEV", "false")
        setup_logging()
        log = get_logger("test")
        log.info("prod mode test")

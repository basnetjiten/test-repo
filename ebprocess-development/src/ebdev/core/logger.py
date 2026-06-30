# -*- coding: utf-8 -*-
"""Logging configuration for ebprocess-development."""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """Get a configured Logger instance."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
            )
        )
        logger.addHandler(handler)
    # Ensure it inherits the level
    logger.propagate = True
    return logger

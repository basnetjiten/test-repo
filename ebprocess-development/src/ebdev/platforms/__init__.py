# -*- coding: utf-8 -*-
"""Platforms strategy interface for ebprocess-development."""

from ebdev.platforms.base import PlatformStrategy
from ebdev.platforms.factory import get_platform_strategy

__all__ = [
    "PlatformStrategy",
    "get_platform_strategy",
]

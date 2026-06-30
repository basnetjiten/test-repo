# -*- coding: utf-8 -*-
"""Factory registry mapping platform strings to Strategy singletons."""

from __future__ import annotations

from ebdev.platforms.base import PlatformStrategy
from ebdev.platforms.flutter import FlutterStrategy
from ebdev.platforms.api import ApiStrategy
from ebdev.platforms.web import WebStrategy
from ebdev.platforms.cms import CmsStrategy
from ebdev.core.exceptions import UnsupportedPlatformError

# Strategy Cache Singletons
_STRATEGY_MAP: dict[str, PlatformStrategy] = {
    "flutter": FlutterStrategy(),
    "api": ApiStrategy(),
    "web": WebStrategy(),
    "cms": CmsStrategy(),
}


def get_platform_strategy(platform: str) -> PlatformStrategy:
    """
    Resolve platform string to PlatformStrategy object.

    Args:
        platform: Platform name ("flutter", "api", "web", "cms").

    Returns:
        PlatformStrategy concrete implementation.

    Raises:
        ValueError: If platform is unsupported.
    """
    key = platform.strip().lower()
    if key not in _STRATEGY_MAP:
        raise UnsupportedPlatformError(
            f"Unsupported platform '{platform}'. "
            f"Supported platforms: {list(_STRATEGY_MAP.keys())}"
        )
    return _STRATEGY_MAP[key]

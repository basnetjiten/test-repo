# -*- coding: utf-8 -*-
"""
factory.py
==========
Factory registry mapping platform strings to Strategy singletons.

Responsibilities
----------------
* Manage mapping singletons for different execution platforms.
* Resolve platform name strings to their concrete strategy handlers.
"""

from __future__ import annotations

from ebdev.core.exceptions import UnsupportedPlatformError
from ebdev.platforms.api import ApiStrategy
from ebdev.platforms.base import PlatformStrategy
from ebdev.platforms.cms import CmsStrategy
from ebdev.platforms.flutter import FlutterStrategy
from ebdev.platforms.web import WebStrategy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Strategy Cache Singletons
_STRATEGY_MAP: dict[str, PlatformStrategy] = {
    "flutter": FlutterStrategy(),
    "api": ApiStrategy(),
    "web": WebStrategy(),
    "cms": CmsStrategy(),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_platform_strategy(platform: str) -> PlatformStrategy:
    """
    Resolve platform string to PlatformStrategy object.

    Parameters
    ----------
    platform : str
        Platform name ("flutter", "api", "web", "cms").

    Returns
    -------
    PlatformStrategy
        Concrete implementation for the platform.

    Raises
    ------
    UnsupportedPlatformError
        If the platform name is unsupported.
    """
    key = platform.strip().lower()
    if key not in _STRATEGY_MAP:
        raise UnsupportedPlatformError(
            f"Unsupported platform '{platform}'. Supported platforms: {list(_STRATEGY_MAP.keys())}"
        )
    return _STRATEGY_MAP[key]

"""Declarative, limits-aware Discord Components V2 UI framework.

Views describe intent (semantic nodes plus overflow policies); the engine measures chrome,
allocates Discord's display budgets, and materializes discord.py V2 component trees that can
never exceed a platform limit.

This package must stay free of `squid.*` imports and of `_()` i18n markers: all user-facing
text enters pre-translated through `Chrome`.
"""

from squid_layouts.conform import ELLIPSIS, LimitViolationError, conform, conform_modal, trim
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.testing import assert_within_limits

__all__ = [
    "ELLIPSIS",
    "LIMITS",
    "LimitViolationError",
    "V2Limits",
    "assert_within_limits",
    "conform",
    "conform_modal",
    "trim",
]

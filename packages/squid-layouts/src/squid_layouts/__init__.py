"""Declarative, limits-aware Discord Components V2 UI framework.

Views describe intent (semantic nodes plus overflow policies); the engine measures chrome,
allocates Discord's display budgets, and materializes discord.py V2 component trees that can
never exceed a platform limit.

This package must stay free of `squid.*` imports and of `_()` i18n markers: all user-facing
text enters pre-translated through `Chrome`.
"""

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.conform import ELLIPSIS, LimitViolationError, conform, conform_modal, trim
from squid_layouts.constraints import Drop, Never, Overflow, Spill, Truncate
from squid_layouts.ir import (
    Code,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    Node,
    Panel,
    RawItem,
    Row,
    Section,
    Sep,
    Text,
    Thumbnail,
)
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.materialize import StaticView, materialize, render_static
from squid_layouts.presets import Field, FieldGroup, banner, card, listing, report
from squid_layouts.solve import LayoutOverflowError, SolvedLayout, solve
from squid_layouts.testing import assert_within_limits

__all__ = [
    "DEFAULT_CHROME",
    "ELLIPSIS",
    "LIMITS",
    "Chrome",
    "Code",
    "Drop",
    "Field",
    "FieldGroup",
    "Footer",
    "Gallery",
    "Heading",
    "LayoutOverflowError",
    "LimitViolationError",
    "Lines",
    "LinkButton",
    "Never",
    "Node",
    "Overflow",
    "Panel",
    "RawItem",
    "Row",
    "Section",
    "Sep",
    "SolvedLayout",
    "Spill",
    "StaticView",
    "Text",
    "Thumbnail",
    "Truncate",
    "V2Limits",
    "assert_within_limits",
    "banner",
    "card",
    "conform",
    "conform_modal",
    "listing",
    "materialize",
    "render_static",
    "report",
    "solve",
    "trim",
]

"""Declarative, limits-aware Discord Components V2 UI framework.

Views describe intent (semantic nodes plus overflow policies); the engine measures chrome,
allocates Discord's display budgets, and materializes discord.py V2 component trees that can
never exceed a platform limit.

This package must stay free of `squid.*` imports and of `_()` i18n markers: all user-facing
text enters pre-translated through `Chrome`.
"""

from squid_layouts import deliver
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.component import Component, state
from squid_layouts.conform import ELLIPSIS, LimitViolationError, conform, conform_modal, trim
from squid_layouts.constraints import Alt, Alts, Drop, Never, Overflow, Paginate, Spill, Truncate, alts
from squid_layouts.ir import (
    Button,
    Code,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    RawItem,
    Row,
    Section,
    SelectMenu,
    Sep,
    Text,
    Thumbnail,
)
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.materialize import StaticView, materialize, render_static
from squid_layouts.modal import LabelSpec, ModalSpec, TextInputSpec, build_modal
from squid_layouts.mount import ErrorHook, Mount, MountedView
from squid_layouts.navigation import Navigator
from squid_layouts.presets import Field, FieldGroup, banner, card, listing, report
from squid_layouts.runtime import Reactor
from squid_layouts.solve import LayoutOverflowError, SolvedLayout, solve
from squid_layouts.testing import assert_within_limits

__all__ = [
    "DEFAULT_CHROME",
    "ELLIPSIS",
    "LIMITS",
    "Alt",
    "Alts",
    "Button",
    "Chrome",
    "Code",
    "Component",
    "Drop",
    "ErrorHook",
    "Field",
    "FieldGroup",
    "Footer",
    "Gallery",
    "Heading",
    "LabelSpec",
    "LayoutOverflowError",
    "LimitViolationError",
    "Lines",
    "LinkButton",
    "ModalSpec",
    "Mount",
    "MountedView",
    "Navigator",
    "Never",
    "Node",
    "Option",
    "Overflow",
    "Paginate",
    "Panel",
    "RawItem",
    "Reactor",
    "Row",
    "Section",
    "SelectMenu",
    "Sep",
    "SolvedLayout",
    "Spill",
    "StaticView",
    "Text",
    "TextInputSpec",
    "Thumbnail",
    "Truncate",
    "V2Limits",
    "alts",
    "assert_within_limits",
    "banner",
    "build_modal",
    "card",
    "conform",
    "conform_modal",
    "deliver",
    "listing",
    "materialize",
    "render_static",
    "report",
    "solve",
    "state",
    "trim",
]

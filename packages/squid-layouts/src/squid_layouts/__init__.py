"""Declarative, limits-aware Discord Components V2 UI framework.

Views describe intent (semantic nodes plus overflow policies); the engine measures chrome,
allocates Discord's display budgets, and materializes discord.py V2 component trees that can
never exceed a platform limit.

This package must stay free of `squid.*` imports and of `_()` i18n markers: all user-facing
text enters pre-translated through `Chrome`.
"""

from squid_layouts import deliver
from squid_layouts.actions import ActionEvent, ActionPolicy, Actor, PressEvent, SelectionEvent, SubmitEvent, Visibility
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.component import Component
from squid_layouts.compositor import Composition, compose, render_static
from squid_layouts.conform import ELLIPSIS, LimitViolationError, conform, conform_modal, trim
from squid_layouts.constraints import Alt, Alts, Drop, Never, Overflow, Paginate, Spill, Truncate, alts
from squid_layouts.document import Asset, Document, InlineAsset, StoredAsset, as_document
from squid_layouts.durability import (
    ComponentRegistry,
    ComponentSnapshot,
    MemorySnapshotStore,
    MountManager,
    MountSnapshot,
    SnapshotCodec,
    SnapshotError,
    SnapshotStore,
)
from squid_layouts.errors import DrawInvariantError, LayoutDegradedError, LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.html import DISCORD_PREVIEW_CSS, HtmlRenderer
from squid_layouts.ir import (
    ActionGroup,
    Button,
    Choice,
    Code,
    Embed,
    Extension,
    Fold,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    MediaCollection,
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
    Variant,
    as_nodes,
)
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.materialize import StaticView, materialize
from squid_layouts.modal import LabelSpec, ModalSpec, TextInputSpec, build_modal
from squid_layouts.mount import ErrorHook, Mount, MountedView
from squid_layouts.navigation import Navigator
from squid_layouts.pagination import NavFactory, PageContext, default_nav, page_controls
from squid_layouts.planner import plan
from squid_layouts.presets import Field, FieldGroup, banner, card, listing, report
from squid_layouts.reactivity import ReactiveWriteError, batch, computed, state, transaction
from squid_layouts.runtime import Reactor
from squid_layouts.scene import PlanEvent, PlanReport, PlanResult, SceneDocument
from squid_layouts.scene_codec import SceneCodec, SceneCodecError
from squid_layouts.solve import LayoutOverflowError, SolvedLayout, solve
from squid_layouts.styles import ActionStyle, Color
from squid_layouts.target import PreparedExtension, ResourceCost, TargetProfile
from squid_layouts.testing import assert_within_limits

__all__ = [
    "DEFAULT_CHROME",
    "DISCORD_PREVIEW_CSS",
    "ELLIPSIS",
    "LIMITS",
    "ActionEvent",
    "ActionGroup",
    "ActionPolicy",
    "ActionStyle",
    "Actor",
    "Alt",
    "Alts",
    "Asset",
    "Button",
    "Choice",
    "Chrome",
    "Code",
    "Color",
    "Component",
    "ComponentRegistry",
    "ComponentSnapshot",
    "Composition",
    "Document",
    "DrawInvariantError",
    "Drop",
    "Embed",
    "ErrorHook",
    "Extension",
    "Field",
    "FieldGroup",
    "Fold",
    "Footer",
    "Gallery",
    "Heading",
    "HtmlRenderer",
    "InlineAsset",
    "LabelSpec",
    "LayoutDegradedError",
    "LayoutInvariantError",
    "LayoutOverflowError",
    "LimitViolationError",
    "Lines",
    "LinkButton",
    "MediaCollection",
    "MemorySnapshotStore",
    "ModalSpec",
    "Mount",
    "MountManager",
    "MountSnapshot",
    "MountedView",
    "NavFactory",
    "Navigator",
    "Never",
    "Node",
    "Option",
    "Overflow",
    "PageContext",
    "Paginate",
    "Panel",
    "PlanEvent",
    "PlanReport",
    "PlanResult",
    "PreparedExtension",
    "PressEvent",
    "RawItem",
    "ReactiveWriteError",
    "Reactor",
    "ResourceCost",
    "Row",
    "SceneCodec",
    "SceneCodecError",
    "SceneDocument",
    "Section",
    "SelectMenu",
    "SelectionEvent",
    "Sep",
    "SnapshotCodec",
    "SnapshotError",
    "SnapshotStore",
    "SolvedLayout",
    "Spill",
    "StaticView",
    "StoredAsset",
    "SubmitEvent",
    "TargetProfile",
    "Text",
    "TextInputSpec",
    "Thumbnail",
    "Truncate",
    "UnsolvableLayoutError",
    "V2Limits",
    "Variant",
    "Visibility",
    "alts",
    "as_document",
    "as_nodes",
    "assert_within_limits",
    "banner",
    "batch",
    "build_modal",
    "card",
    "compose",
    "computed",
    "conform",
    "conform_modal",
    "default_nav",
    "deliver",
    "listing",
    "materialize",
    "page_controls",
    "plan",
    "render_static",
    "report",
    "solve",
    "state",
    "transaction",
    "trim",
]

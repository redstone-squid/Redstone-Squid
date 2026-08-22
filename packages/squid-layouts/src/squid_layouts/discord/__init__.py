"""Discord Components V2 target, renderer, and runtime adapter."""

from squid_layouts.discord import delivery, durability, live
from squid_layouts.discord.actions import ActionResponder, native, responder
from squid_layouts.discord.compose import Composition, compose, render_static
from squid_layouts.discord.conform import ELLIPSIS, LimitViolationError, conform, conform_modal, trim
from squid_layouts.discord.delivery import (
    DeliveryAbandoned,
    DeliveryReceipt,
    Destination,
    EditHandle,
    StaleHandleError,
    reply_to,
    respond_to,
)
from squid_layouts.discord.live import mounts
from squid_layouts.discord.modal import (
    EntityField,
    EntityType,
    FileField,
    LabelSpec,
    ModalSpec,
    TextInputSpec,
    build_form_modal,
    build_modal,
)
from squid_layouts.discord.mount import (
    ErrorHook,
    FinishHook,
    Mount,
    MountAddress,
    MountedView,
    MountSnapshot,
)
from squid_layouts.discord.navigation import Navigator
from squid_layouts.discord.reactor import Reactor
from squid_layouts.discord.renderer import Renderer, RoutedItem, StaticView, Wire
from squid_layouts.discord.routing import (
    Middleware,
    RouteComponent,
    RouteDescription,
    RouteGroup,
    RouteHandler,
    RouteLike,
    RouteNext,
    Router,
    RouteRequest,
)
from squid_layouts.discord.target import DEFAULT_TARGET, NativeItem, Target
from squid_layouts.planning.limits import LIMITS as DEFAULT_LIMITS
from squid_layouts.planning.limits import V2Limits as Limits
from squid_layouts.planning.navigation import (
    NavFactory,
    NavigationContext,
    NavigationState,
    default_nav,
    navigation_controls,
)

__all__ = [
    "DEFAULT_LIMITS",
    "DEFAULT_TARGET",
    "ELLIPSIS",
    "ActionResponder",
    "Composition",
    "DeliveryAbandoned",
    "DeliveryReceipt",
    "Destination",
    "EditHandle",
    "EntityField",
    "EntityType",
    "ErrorHook",
    "FileField",
    "FinishHook",
    "LabelSpec",
    "LimitViolationError",
    "Limits",
    "Middleware",
    "ModalSpec",
    "Mount",
    "MountAddress",
    "MountSnapshot",
    "MountedView",
    "NativeItem",
    "NavFactory",
    "NavigationContext",
    "NavigationState",
    "Navigator",
    "Reactor",
    "Renderer",
    "RouteComponent",
    "RouteDescription",
    "RouteGroup",
    "RouteHandler",
    "RouteLike",
    "RouteNext",
    "RouteRequest",
    "RoutedItem",
    "Router",
    "StaleHandleError",
    "StaticView",
    "Target",
    "TextInputSpec",
    "Wire",
    "build_form_modal",
    "build_modal",
    "compose",
    "conform",
    "conform_modal",
    "default_nav",
    "delivery",
    "durability",
    "live",
    "mounts",
    "native",
    "navigation_controls",
    "render_static",
    "reply_to",
    "respond_to",
    "responder",
    "trim",
]

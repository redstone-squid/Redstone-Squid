"""Discord Components V2 target, renderer, and runtime adapter."""

from squid_layouts.discord import delivery, durability
from squid_layouts.discord.actions import ActionResponder, native, responder
from squid_layouts.discord.compose import Composition, compose, render_static
from squid_layouts.discord.conform import ELLIPSIS, LimitViolationError, conform, conform_modal, trim
from squid_layouts.discord.delivery import (
    DeliveryAbandoned,
    Destination,
    EditHandle,
    StaleHandleError,
    reply_to,
    respond_to,
)
from squid_layouts.discord.modal import LabelSpec, ModalSpec, TextInputSpec, build_modal
from squid_layouts.discord.mount import ErrorHook, Mount, MountedView
from squid_layouts.discord.navigation import Navigator
from squid_layouts.discord.reactor import Reactor
from squid_layouts.discord.renderer import Renderer, StaticView, Wire
from squid_layouts.discord.routing import RouteHandler, Router
from squid_layouts.discord.target import DEFAULT_TARGET, NativeItem, Target
from squid_layouts.planning.limits import LIMITS as DEFAULT_LIMITS
from squid_layouts.planning.limits import V2Limits as Limits
from squid_layouts.planning.pagination import NavFactory, PageContext, default_nav, page_controls

__all__ = [
    "DEFAULT_LIMITS",
    "DEFAULT_TARGET",
    "ELLIPSIS",
    "ActionResponder",
    "Composition",
    "DeliveryAbandoned",
    "Destination",
    "EditHandle",
    "ErrorHook",
    "LabelSpec",
    "LimitViolationError",
    "Limits",
    "ModalSpec",
    "Mount",
    "MountedView",
    "NativeItem",
    "NavFactory",
    "Navigator",
    "PageContext",
    "Reactor",
    "Renderer",
    "RouteHandler",
    "Router",
    "StaleHandleError",
    "StaticView",
    "Target",
    "TextInputSpec",
    "Wire",
    "build_modal",
    "compose",
    "conform",
    "conform_modal",
    "default_nav",
    "delivery",
    "durability",
    "native",
    "page_controls",
    "render_static",
    "reply_to",
    "respond_to",
    "responder",
    "trim",
]

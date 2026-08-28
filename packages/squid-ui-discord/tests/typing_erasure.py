"""Pins for the erased aliases and the finished `Axis` keying. Nothing here runs.

`Component[RenderTargetT]` and `MessageRoot[RenderTargetT, AdapterT]` default every type parameter, so a bare
annotation silently means one specific instantiation and rejects every other -- including `Self`
inside each class's own methods. That is what `AnyComponent` and `AnyMessageRoot` exist to say,
and these pins are what keeps them saying it: if a bare name ever becomes assignable from a
non-default render target, the alias has stopped being load-bearing and the defaults have changed
meaning underneath it.

Every `pyrefly: ignore` below is an assertion that the line *is* an error. If one goes unused,
the guarantee it protects has changed.
"""

from typing import assert_type

import discord

import squid_ui as sl
from squid_ui.planning import ClassicTarget, ComponentsV2Target
from squid_ui.planning.adapter import ResourceCost
from squid_ui.planning.limits import LIMITS, Axis
from squid_ui.runtime.component import AnyComponent, RenderResult
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.emoji import discord_emoji
from squid_ui_discord.message_root import AnyMessageRoot
from squid_ui_discord.target import classic, v2


class ClassicPanel(sl.Component[ClassicTarget]):
    def render(self) -> RenderResult[ClassicTarget]:
        return sl.stack(sl.heading("title"))


class V2Panel(sl.Component[ComponentsV2Target]):
    def render(self) -> RenderResult[ComponentsV2Target]:
        return sl.stack(sl.heading("title"))


# --- the erased aliases hold a mount or component for any render target -----------------------------

classic_mount = MessageRoot(ClassicPanel(), access=Everyone(), target=classic())
v2_mount = MessageRoot(V2Panel(), access=Everyone())

# The mount target is part of the component contract, not an independent runtime option.
MessageRoot(ClassicPanel(), access=Everyone())  # pyrefly: ignore[bad-argument-type]
MessageRoot(V2Panel(), access=Everyone(), target=classic())  # pyrefly: ignore[no-matching-overload]
MessageRoot(ClassicPanel(), access=Everyone(), target=v2())  # pyrefly: ignore[no-matching-overload]


def takes_any_mount(mount: AnyMessageRoot) -> None: ...


def takes_any_component(component: AnyComponent) -> None: ...


takes_any_mount(classic_mount)
takes_any_mount(v2_mount)
takes_any_component(ClassicPanel())
takes_any_component(V2Panel())


# --- while the bare names mean their defaults, which is the trap the aliases exist for -----


def takes_bare_mount(mount: MessageRoot) -> None: ...


def takes_bare_component(component: sl.Component) -> None: ...


takes_bare_mount(classic_mount)  # pyrefly: ignore[bad-argument-type]
takes_bare_component(ClassicPanel())  # pyrefly: ignore[bad-argument-type]


# --- a resource cost is keyed by the axis enum, not by whatever string ---------------------

cost = ResourceCost({Axis.COMPONENTS: 3, Axis.DISPLAY_TEXT: 120})
assert_type(cost.get(Axis.COMPONENTS), int)
assert_type(cost.axes, tuple[Axis, ...])

ResourceCost({"components": 3})  # pyrefly: ignore[bad-assignment]
cost.get("components")  # pyrefly: ignore[bad-argument-type]

# The limits side already spoke `Axis`; this is the hand-off that used to be untypeable.
assert not list(cost.over(LIMITS.capacities))


# --- `discord_emoji` takes what the nodes holding an emoji actually declare ----------------

assert_type(discord_emoji("\N{LARGE RED CIRCLE}"), str | discord.PartialEmoji | None)

"""Build targets and views, or drive isolated message-root renders in tests."""

from copy import copy
from dataclasses import replace
from typing import Any

import anyio

import discord
from squid_ui import scene
from squid_ui.ext.discord.limits import LIMITS, MessageLimits, V2Limits
from squid_ui.ext.discord.messages.root import MessageRoot
from squid_ui.ext.discord.messages.scheduler import MessageRootScheduler
from squid_ui.ext.discord.messages.wiring import AnyMountedView, ClassicMountedView, MountedView
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.ext.discord.rendering import render_static
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.target_types import ComponentsV2Target, DiscordAdapter


def without_capabilities[LimitsT: MessageLimits, BodyT: scene.Body, RenderTargetT, AdapterT](
    target: Target[LimitsT, BodyT, RenderTargetT, AdapterT], *capabilities: str
) -> Target[LimitsT, BodyT, RenderTargetT, AdapterT]:
    """A copy of `target` whose dialect declares fewer protocol capabilities.

    Every degradation path is reached by a target that lacks something, and a protocol
    capability is a fact about the dialect rather than a field on the target — so removing
    one means a dialect that declares less. Adapter capabilities are not affected; use
    `Target.restrict_adapter_capabilities` for those.
    """
    reduced = copy(target.dialect)
    reduced.capabilities = frozenset(target.dialect.capabilities) - {*capabilities}
    return replace(target, dialect=reduced)


def commit_render(message_root: MessageRoot, *, disabled: bool = False) -> MountedView:
    """Commit a staged V2 render without loading components or delivering to Discord.

    This advances handlers and generation directly for synchronous render assertions.
    Use ``await root.send(delivered_to(message_harness()))`` to exercise the load and
    send lifecycle.
    """
    view = _commit(message_root, disabled=disabled)
    assert isinstance(view, MountedView), "this message root draws a classic message; use commit_classic_render"
    return view


def commit_classic_render(message_root: MessageRoot, *, disabled: bool = False) -> ClassicMountedView:
    """Commit a classic render without loading or delivery, asserting its native view type."""
    view = _commit(message_root, disabled=disabled)
    assert isinstance(view, ClassicMountedView), "this message root draws a Components V2 message; use commit_render"
    return view


def _commit(message_root: MessageRoot, *, disabled: bool) -> AnyMountedView:
    view = message_root._stage_view(disabled=disabled)
    candidate = message_root._candidates.pending
    assert candidate is not None and candidate.view is view
    message_root._commit(candidate)
    return view


def target_profile(
    name: str = "test", *, capabilities: frozenset[AdapterCapability] = frozenset(), limits: V2Limits = LIMITS
) -> Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordAdapter]:
    """A V2 target whose adapter supplies exactly `capabilities` and no extensions.

    Capabilities that are not Discord protocol facts belong to the adapter axis, which is what
    lets a test vary them without inventing a dialect. Use `without_capabilities` to take a
    *protocol* capability away from a target that already exists.
    """
    return components_v2_target(AdapterProfile(DiscordAdapter, name, ">=1", capabilities=capabilities), limits=limits)


def static_view(*args: Any, **kwargs: Any) -> discord.ui.LayoutView:
    """The drawn layout of a sessionless V2 document, for tests that only read components."""
    return render_static(*args, **kwargs).layout


def layout_view(*items: discord.ui.Item[Any], timeout: float | None = None) -> discord.ui.LayoutView:
    """A host-owned discord.py view holding `items` -- the shape adoption and conform start from."""
    view = discord.ui.LayoutView(timeout=timeout)
    for item in items:
        view.add_item(item)
    return view


def action_row(*items: discord.ui.Item[Any]) -> discord.ui.ActionRow[Any]:
    """A discord.py action row holding `items`."""
    row: discord.ui.ActionRow[Any] = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


async def drain(scheduler: MessageRootScheduler, *, timeout: float = 1) -> None:
    """Run the scheduler until its queue join completes, then cancel it.

    The timeout is in seconds. Newly queued work also contributes to the join, so tests
    must avoid producers that enqueue forever.
    """
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scheduler.run)
        with anyio.fail_after(timeout):
            await scheduler._queue.join()
        tasks.cancel_scope.cancel()

"""Composing classic Discord messages: `squid_discord.classic.compose` and friends.

A separate module rather than a mode flag on `squid_discord.compose`, because the author picks
the message mode and should have to say so. The two produce different messages with different
capabilities, and a default that silently decides which one you get is the thing this whole
target exists to avoid.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import discord

from squid_discord.adapter import require_discord_py_target
from squid_discord.attachments import files_for
from squid_discord.classic_renderer import ClassicRenderer, Wire
from squid_discord.fragments import _reject_dispatchable
from squid_discord.inspection import (
    CustomIdSite,
    DiscordReservation,
    audit_classic_payload,
    effective_rows,
    measure,
    measure_classic,
)
from squid_discord.presentation import DiscordMode, DiscordModeError, DiscordPresentation
from squid_discord.target import DISCORD_V1_DPY27, Target
from squid_layouts import scene
from squid_layouts.assets import Asset
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.document import DocumentLike
from squid_layouts.errors import ExistingLayoutError
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.adapter import AdapterCapability
from squid_layouts.planning.cache import PlanCache, PlanMemo
from squid_layouts.planning.limits import CLASSIC_LIMITS, Axis, ClassicLimits
from squid_layouts.planning.navigation import PlannedNav
from squid_layouts.planning.planner import EMPTY_RESERVATION
from squid_layouts.planning.planner import plan as plan_document
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET
from squid_layouts.planning.target import ResourceCost
from squid_layouts.profiling import OperationRecorder
from squid_layouts.runtime.component import Component
from squid_layouts.runtime.presentation import PresentationSession
from squid_layouts.scene.model import PlanReport, PlanResult
from squid_layouts.sources import Position
from squid_layouts.target_types import ClassicTarget, DiscordPyAdapter
from squid_layouts.text import NEUTRAL, Localization

logger = logging.getLogger(__name__)


def compose(
    rendered: DocumentLike,
    *,
    wire: Wire | None = None,
    renderer: ClassicRenderer | None = None,
    target: Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, DiscordPyAdapter] = DISCORD_V1_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    positions: Mapping[str, Position] | None = None,
    nav: PlannedNav | None = None,
    session: PresentationSession | None = None,
    cache: PlanCache | None = None,
    memo: PlanMemo | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    profile: OperationRecorder | None = None,
):
    """Plan a logical document, then draw the complete classic message it resolves to."""
    from squid_discord.composition import Composition, _span

    adapter = require_discord_py_target(target, AdapterCapability.RENDER_CLASSIC, "compose a classic message")
    with _span(profile, "planner") as planner_span:
        result = plan_document(
            rendered,
            target=target,
            chrome=chrome,
            localization=localization,
            palette=palette,
            strict=strict,
            reservation=reservation,
            positions=positions,
            nav=nav,
            session=session,
            cache=cache,
            memo=memo,
            search_budget=search_budget,
        )
        if planner_span is not None:
            planner_span.set_attribute("cache_hit", result.metrics.cache_hit)
            planner_span.set_attribute("states_explored", result.metrics.states_explored)
            planner_span.set_attribute("search_fallback", result.metrics.search_fallback)
        if profile is not None:
            profile.increment("planner.calls")
            profile.increment("planner.cache_hits", int(result.metrics.cache_hit))
            profile.increment("planner.search_fallbacks", int(result.metrics.search_fallback))
            profile.increment("planner.states_explored", result.metrics.states_explored)
    drawer = renderer if renderer is not None else ClassicRenderer(limits=target.limits, adapter=adapter)
    with _span(profile, "renderer"):
        presentation = drawer.draw(result.scene, plan=result, wire=wire)
    if result.report.events:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
    return Composition(presentation, result)


def render_static(
    nodes: DocumentLike | Component,
    *,
    target: Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, DiscordPyAdapter] = DISCORD_V1_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
) -> DiscordPresentation:
    """Plan and draw a sessionless classic document as one complete message.

    A presentation, never a bare view: the embeds *are* the message here, and handing back
    only the controls would leave the caller to reassemble the half that carries the content.
    """
    return compose(
        nodes.render() if isinstance(nodes, Component) else nodes,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
    ).presentation


@dataclass(slots=True)
class AttachedClassicContribution:
    """A Squid region placed into a host-owned classic message, and how to take it back.

    The host's `View` is mutated transactionally: its items are moved, never cloned, because
    a control's callback registration belongs to the view that owns it. Content, embeds, and
    the asset tuple are immutable values, so `presentation` is a new whole message rather
    than a patch applied to the host's.
    """

    presentation: DiscordPresentation
    plan: PlanResult
    view: discord.ui.View | None
    items: tuple[discord.ui.Item[Any], ...]
    """Exactly the items inserted, by identity, so `remove` cannot take a lookalike."""
    assets: tuple[Asset, ...]
    fingerprint: str

    @property
    def report(self) -> PlanReport:
        """The plan's degradation report, so a one-call contribution never hides one."""
        return self.plan.report

    def build_files(self) -> list[discord.File]:
        return files_for(self.assets)

    def stale(self) -> bool:
        """Whether the host message has changed since this region was planned against it."""
        return measure(self.presentation).fingerprint != self.fingerprint

    def remove(self) -> None:
        """Remove exactly the items this contribution inserted.

        Identity-based, so a host replacement carrying the same custom id is never mistaken
        for the item it replaced.
        """
        if self.view is None:
            return
        for item in self.items:
            if any(child is item for child in self.view.children):
                self.view.remove_item(item)


def measure_host(
    host: DiscordPresentation,
    *,
    attachments: int = 0,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> DiscordReservation:
    """What a host-owned classic message already spends. Mutates and repairs nothing."""
    return measure_classic(host, attachments=attachments, limits=limits)


def contribute(
    document: DocumentLike,
    *,
    to: DiscordPresentation,
    followed_by: Sequence[discord.Embed] = (),
    followed_by_controls: Sequence[discord.ui.Item[Any]] = (),
    reserve: ResourceCost = EMPTY_RESERVATION,
    attachments: int = 0,
    target: Target = DISCORD_V1_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    positions: Mapping[str, Position] | None = None,
) -> AttachedClassicContribution:
    """Plan a region into what a host-owned classic message leaves unspent, and place it.

    Squid replaces whole embeds and whole control regions, never anything finer. Splicing
    fields into a host-authored embed would mean owning that embed's internal layout and
    overflow policy without owning the embed, so it is not offered.

    Contributed controls stay limited to links and routed actions. The host view's callbacks
    remain under its owner, arbitrary native callback items cannot enter, and a contributed
    region is never reactive independently of the host.

    This never sends: delivery stays with the owner of the message. Note that routed controls
    do not run the host view's checks and do not refresh its timeout.
    """
    if to.mode is not DiscordMode.CLASSIC:
        message = f"classic.contribute needs a classic host presentation, not {to.mode.value}"
        raise DiscordModeError(message)
    limits = target.limits
    trailing_embeds = tuple(followed_by)
    trailing_controls = tuple(followed_by_controls)

    host = measure_classic(to, attachments=attachments, limits=limits)
    host.raise_if_invalid()

    reservation = host.reserved + reserve + _trailing_cost(trailing_embeds, trailing_controls)
    composition = compose(
        document,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
        positions=positions,
    )
    staged = composition.presentation
    staging = staged.view if isinstance(staged.view, discord.ui.View) else None
    if staging is not None:
        _reject_dispatchable(staging)

    host_view = to.view if isinstance(to.view, discord.ui.View) else None
    staged_items = tuple(staging.children) if staging is not None else ()
    merged = DiscordPresentation.classic(
        content=to.content if to.content is not None else staged.content,
        embeds=(*to.embeds, *staged.embeds, *trailing_embeds),
        view=host_view if host_view is not None else staging,
        assets=(*to.assets, *staged.assets),
    )

    problems = audit_classic_payload(
        content=merged.content,
        embeds=merged.embeds,
        view=None,
        attachments=attachments + len(merged.assets),
        limits=limits,
    )
    problems.extend(
        _preflight_controls(host_view, staged_items, trailing_controls, limits=limits, existing=host.custom_ids)
    )
    if problems:
        # Nothing has moved yet. Preflight is complete before mutation precisely so a
        # rejected contribution leaves the host exactly as it was found.
        raise ExistingLayoutError(problems)

    inserted = _insert(host_view, staging, staged_items, trailing_controls)
    return AttachedClassicContribution(
        presentation=merged,
        plan=composition.plan,
        view=merged.view if isinstance(merged.view, discord.ui.View) else None,
        items=inserted,
        assets=merged.assets,
        fingerprint=measure(merged).fingerprint,
    )


def _trailing_cost(embeds: Sequence[discord.Embed], controls: Sequence[discord.ui.Item[Any]]) -> ResourceCost:
    """Room the host will need for what it has not added yet."""
    return ResourceCost(
        {
            Axis.EMBEDS: len(embeds),
            Axis.EMBED_TEXT: sum(len(embed) for embed in embeds),
            Axis.CONTROLS: len(controls),
        }
    )


def _preflight_controls(
    host_view: discord.ui.View | None,
    staged: Sequence[discord.ui.Item[Any]],
    trailing: Sequence[discord.ui.Item[Any]],
    *,
    limits: ClassicLimits,
    existing: Sequence[CustomIdSite],
) -> list[str]:
    """Prove the whole prospective view is legal before one item moves."""
    problems: list[str] = []
    additions = (*staged, *trailing)
    host_children = list(host_view.children) if host_view is not None else []
    total = len(host_children) + len(additions)
    if total > limits.controls:
        problems.append(f"{total} view children exceed {limits.controls}")

    used = len({*effective_rows(host_view)}) if host_view is not None and host_children else 0
    needed = _rows_needed(staged) + _rows_needed(trailing)
    if used + needed > limits.rows:
        problems.append(f"{used + needed} action rows exceed {limits.rows}")

    seen = {site.custom_id for site in existing}
    for item in additions:
        custom_id = getattr(item, "custom_id", None)
        if not isinstance(custom_id, str):
            continue
        if custom_id in seen:
            problems.append(f"custom id {custom_id!r} is already used in this message")
        seen.add(custom_id)
    problems.extend(
        f"trailing item {type(item).__name__} already belongs to another view"
        for item in trailing
        if item.view is not None and item.view is not host_view
    )
    return problems


def _rows_needed(items: Sequence[discord.ui.Item[Any]]) -> int:
    return len({item.row for item in items}) if items else 0


def _insert(
    host_view: discord.ui.View | None,
    staging: discord.ui.View | None,
    staged: Sequence[discord.ui.Item[Any]],
    trailing: Sequence[discord.ui.Item[Any]],
) -> tuple[discord.ui.Item[Any], ...]:
    """Move staged controls into the host view, rolling every insertion back on failure."""
    if host_view is None or not (staged or trailing):
        return tuple(staged)
    offset = (max(effective_rows(host_view)) + 1) if host_view.children else 0
    if staging is not None:
        for item in staged:
            staging.remove_item(item)

    added: list[discord.ui.Item[Any]] = []
    try:
        for item in staged:
            item.row = (item.row or 0) + offset
            host_view.add_item(item)
            added.append(item)
        for item in trailing:
            if item.view is not host_view:
                host_view.add_item(item)
                added.append(item)
    except Exception:
        for item in reversed(added):
            host_view.remove_item(item)
        if staging is not None:
            for item in staged:
                if item.view is None:
                    staging.add_item(item)
        raise
    return tuple(added)


__all__ = [
    "CLASSIC_LIMITS",
    "AttachedClassicContribution",
    "ClassicRenderer",
    "compose",
    "contribute",
    "measure_host",
    "render_static",
]

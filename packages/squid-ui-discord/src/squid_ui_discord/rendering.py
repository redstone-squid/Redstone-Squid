"""Plan documents and render complete Discord message payloads."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import discord

from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.adapter import AdapterCapability
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.planner import EMPTY_RESERVATION
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET
from squid_ui.planning.target import ResourceCost
from squid_ui.profiling import OperationRecorder
from squid_ui.runtime.component import Component
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.scene.model import PlanResult
from squid_ui.semantic import LayoutNode
from squid_ui.sources import Position
from squid_ui.target_types import ComponentsV2Target, DiscordPyAdapter
from squid_ui.text import NEUTRAL, Localization
from squid_ui_discord._draw import plan_and_draw
from squid_ui_discord.adapter import require_discord_py_target
from squid_ui_discord.message_payload import MessageModeError, MessagePayload
from squid_ui_discord.renderer import V2Renderer, Wire
from squid_ui_discord.target import DISCORD_V2_DPY27, Target

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RenderedMessage[ViewT: (discord.ui.LayoutView, discord.ui.View | None), BodyT = scene.ComponentsV2]:
    """A resolved plan beside the complete Discord message it draws to.

    Generic over the view because the two modes differ in what they promise. A Components V2
    composition always has a `LayoutView` — it *is* the message. A classic composition has a
    `View` only when the document produced controls, and its embeds carry the rest.
    """

    payload: MessagePayload
    plan: PlanResult[BodyT]

    @property
    def view(self) -> ViewT:
        """The drawn view, typed by which message mode this render is for."""
        return cast(ViewT, self.payload.view)

    @property
    def assets(self) -> tuple[Asset, ...]:
        """Declarative files this rendered message expects to upload."""
        return self.payload.assets

    def build_files(self) -> list[discord.File]:
        """Materialize fresh file wrappers; a sent `discord.File` cannot be re-sent."""
        return self.payload.build_files()

    @property
    def page(self) -> int:
        return self.plan.scene.pagers[0].page if self.plan.scene.pagers else 0

    @property
    def pages(self) -> int:
        return self.plan.scene.pagers[0].pages if self.plan.scene.pagers else 1


def render_message(
    rendered: DocumentLike[ComponentsV2Target],
    *,
    wire: Wire | None = None,
    renderer: V2Renderer | None = None,
    target: Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter] = DISCORD_V2_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    positions: Mapping[str, Position] | None = None,
    nav: PlannedNav | None = None,
    session: PresentationState | None = None,
    cache: PlanCache | None = None,
    memo: PlanMemo | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    profile: OperationRecorder | None = None,
) -> RenderedMessage[discord.ui.LayoutView, scene.ComponentsV2]:
    """Plan a logical document, then draw its resolved Components V2 scene."""
    adapter = require_discord_py_target(target, AdapterCapability.RENDER_V2, "render a Components V2 message")
    payload, result = plan_and_draw(
        rendered,
        PlanRequest(
            target=target,
            chrome=chrome,
            localization=localization,
            palette=palette,
            strict=strict,
            reservation=reservation,
            positions=positions,
            nav=nav,
            session=session,
            search_budget=search_budget,
        ),
        drawer=renderer if renderer is not None else V2Renderer(limits=target.limits, adapter=adapter),
        wire=wire,
        cache=cache,
        memo=memo,
        profile=profile,
    )
    return RenderedMessage(payload, result)


def render_static(
    nodes: DocumentLike[ComponentsV2Target] | Component[ComponentsV2Target],
    *,
    target: Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter] = DISCORD_V2_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
) -> MessagePayload:
    """Plan and draw a sessionless Components V2 document as a complete message."""
    return render_message(
        nodes.render() if isinstance(nodes, Component) else nodes,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
    ).payload


def render_item(
    node: LayoutNode[ComponentsV2Target],
    *,
    target: Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, DiscordPyAdapter] = DISCORD_V2_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    reservation: ResourceCost = EMPTY_RESERVATION,
) -> discord.ui.Item[Any]:
    """Render one node to a detached item, for composition into a host-assembled view.

    Prefer `contribute`, which measures the host view and places the region atomically.
    This is for a caller assembling the surrounding view itself and knowing its own budget,
    which is what `reservation` states.

    Detaching the item from the view the renderer built is legal only here: the renderer owns
    that object, and it is discarded on the way out, so nothing half-built survives the call.

    Raises:
        MessageModeError: The node rendered to no item at all.
    """
    payload = render_static(
        [node], target=target, chrome=chrome, localization=localization, palette=palette, reservation=reservation
    )
    layout = payload.layout
    children = layout.children
    if not children:
        message = "render_item needs a node that draws something; this one produced no item"
        raise MessageModeError(message)
    item = children[0]
    layout.remove_item(item)
    return item

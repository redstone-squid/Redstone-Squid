"""A rendered payload together with the plan needed to publish it."""

from dataclasses import dataclass
from typing import cast

import discord
from squid_ui.content.assets import Asset
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.messages.payload import MessagePayload
from squid_ui.planning.result import PlanResult


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
        """Zero-based active page, or zero when the scene is not paginated."""
        return self.plan.scene.pagers[0].page if self.plan.scene.pagers else 0

    @property
    def pages(self) -> int:
        """Total page count, or one when the scene is not paginated."""
        return self.plan.scene.pagers[0].pages if self.plan.scene.pagers else 1

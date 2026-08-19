"""Discord adapter for portable component action events."""

from io import BytesIO
from typing import TYPE_CHECKING

import discord

from squid_layouts import deliver
from squid_layouts.actions import Visibility
from squid_layouts.document import Asset, InlineAsset
from squid_layouts.modal import ModalSpec, build_modal

if TYPE_CHECKING:
    from squid_layouts.mount import Mount


class DiscordActionResponder:
    """Translate portable response intents onto one Discord interaction."""

    def __init__(self, interaction: discord.Interaction, mount: Mount) -> None:
        self.interaction = interaction
        self.mount = mount

    async def acknowledge(self) -> None:
        if not self.interaction.response.is_done():
            await self.interaction.response.defer()

    async def notice(self, text: str, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        await deliver.respond_text(self.interaction, text, ephemeral=visibility is Visibility.PRIVATE)

    async def present_form(self, form: object) -> None:
        if isinstance(form, ModalSpec):
            modal = build_modal(form, limits=self.mount.limits)
        elif isinstance(form, discord.ui.Modal):
            modal = form
        else:
            message = f"Discord cannot present form type {type(form).__name__}"
            raise TypeError(message)
        if self.interaction.response.is_done():
            message = "Discord modals must be the interaction's initial response"
            raise RuntimeError(message)
        await self.interaction.response.send_modal(modal)

    async def download(self, asset: object) -> None:
        if not isinstance(asset, Asset):
            message = f"Discord cannot download asset type {type(asset).__name__}"
            raise TypeError(message)
        if not isinstance(asset.source, InlineAsset):
            message = "StoredAsset needs a host resolver before Discord delivery"
            raise TypeError(message)
        file = discord.File(BytesIO(asset.source.data), filename=asset.name)
        if self.interaction.response.is_done():
            await self.interaction.followup.send(file=file, ephemeral=True)
        else:
            await self.interaction.response.send_message(file=file, ephemeral=True)

    async def redirect(self, url: str) -> None:
        await self.notice(url)

    async def finish(self) -> None:
        await self.mount.finish_via(self.interaction)

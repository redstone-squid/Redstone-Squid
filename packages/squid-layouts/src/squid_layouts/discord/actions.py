"""Discord adapter for portable component action events."""

from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord

from squid_layouts.actions import ActionEvent, Visibility
from squid_layouts.discord import delivery as deliver
from squid_layouts.discord.modal import ModalSpec, build_modal
from squid_layouts.document import Asset, InlineAsset

if TYPE_CHECKING:
    from squid_layouts.discord.mount import Mount


class ActionResponder:
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
        """Portable entry point; `send_modal` is the typed Discord-scoped one."""
        if not isinstance(form, ModalSpec | discord.ui.Modal):
            message = f"Discord cannot present form type {type(form).__name__}"
            raise TypeError(message)
        await self.send_modal(form)

    async def send_modal(self, form: ModalSpec | discord.ui.Modal) -> None:
        """Present a form Discord can actually show, stated in Discord's own types."""
        modal = build_modal(form, limits=self.mount.limits) if isinstance(form, ModalSpec) else form
        if self.interaction.response.is_done():
            message = "Discord modals must be the interaction's initial response"
            raise RuntimeError(message)
        await self.interaction.response.send_modal(modal)

    async def download(self, asset: object) -> None:
        """Portable entry point; `send_asset` is the typed Discord-scoped one."""
        if not isinstance(asset, Asset):
            message = f"Discord cannot download asset type {type(asset).__name__}"
            raise TypeError(message)
        await self.send_asset(asset)

    async def send_asset(self, asset: Asset) -> None:
        """Deliver an inline asset as a Discord file attachment."""
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

    def invalidate(self) -> None:
        self.mount.invalidate()


def native(event: ActionEvent) -> discord.Interaction[Any]:
    """Return the Discord interaction behind a portable action event.

    The sanctioned escape hatch for handlers that are Discord-only anyway — permission
    checks, nested sends, client lookups. Handlers that must stay frontend-neutral keep
    to `ActionEvent`'s own surface.

    The client parameter is `Any` because the framework cannot know the host's client
    subclass; hosts that care can annotate the binding themselves.

    Raises:
        LookupError: The event came from a frontend other than Discord.
    """
    responder = event.responder
    if not isinstance(responder, ActionResponder):
        frontend = event.context.get("frontend", type(responder).__name__)
        message = f"native() needs a Discord action event, got frontend {frontend!r}"
        # LookupError, not TypeError: the argument is a valid event, the Discord fact is just absent.
        raise LookupError(message)  # noqa: TRY004
    return responder.interaction

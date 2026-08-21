"""Discord adapter for portable component action events."""

from typing import TYPE_CHECKING, Any

import discord

from squid_layouts.actions import ActionEvent, Visibility
from squid_layouts.discord import delivery as deliver
from squid_layouts.discord.modal import ModalSpec, build_modal

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

    async def send_modal(self, form: ModalSpec | discord.ui.Modal) -> None:
        """Present a form, stated in Discord's own types.

        Not part of `sl.ActionResponder`: a form's payload is a frontend object, so no
        portable protocol can type it. Reach this through `sl.discord.responder(event)`.
        """
        modal = build_modal(form, limits=self.mount.limits) if isinstance(form, ModalSpec) else form
        if self.interaction.response.is_done():
            message = "Discord modals must be the interaction's initial response"
            raise RuntimeError(message)
        await self.interaction.response.send_modal(modal)

    async def redirect(self, url: str) -> None:
        await self.notice(url)

    async def finish(self) -> None:
        await self.mount.finish_via(self.interaction)

    def invalidate(self) -> None:
        self.mount.invalidate()


def responder(event: ActionEvent) -> ActionResponder:
    """Return the Discord responder behind a portable action event.

    The sanctioned escape hatch to Discord's native response surfaces — `send_modal` —
    which no portable protocol can type. Handlers that must stay frontend-neutral keep to
    `ActionEvent`'s own methods.

    Raises:
        LookupError: The event came from a frontend other than Discord.
    """
    found = event.responder
    if not isinstance(found, ActionResponder):
        frontend = event.context.get("frontend", type(found).__name__)
        message = f"this event came from frontend {frontend!r}, not Discord"
        # LookupError, not TypeError: the argument is a valid event, the Discord fact is just absent.
        raise LookupError(message)  # noqa: TRY004
    return found


def native(event: ActionEvent) -> discord.Interaction[Any]:
    """Return the Discord interaction behind a portable action event.

    For the Discord-only facts handlers actually need — permission checks, nested sends,
    client lookups. The client parameter is `Any` because the framework cannot know the
    host's client subclass; hosts that care can annotate the binding themselves.

    Raises:
        LookupError: The event came from a frontend other than Discord.
    """
    return responder(event).interaction

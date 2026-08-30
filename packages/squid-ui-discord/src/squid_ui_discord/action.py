"""Owner-bound safe response adapter for component actions."""

from collections.abc import Sequence
from typing import Literal, Unpack, cast, overload

import discord

from squid_ui.forms import FormSpec
from squid_ui.interactions import ActionEvent
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.actions import responder
from squid_ui_discord.contracts import FacadeContent
from squid_ui_discord.facade import DiscordUI
from squid_ui_discord.modal import ModalSpec
from squid_ui_discord.request import DiscordRequest
from squid_ui_discord.response import Response, ResponseOverrides, ResponseResult, ResponseSpec


class DiscordAction[EventT: ActionEvent, OwnerT]:
    """A component event whose response authority ends when its dispatch returns."""

    def __init__(self, event: EventT, ui: DiscordUI[OwnerT]) -> None:
        self.event = event
        self.ui = ui
        native = responder(event)
        interaction = cast(discord.Interaction[discord.Client], native.interaction)
        self._request = DiscordRequest(
            ui,
            interaction,
            native.message_root.localization,
            interaction.user,
            cast(discord.Guild | None, getattr(interaction, "guild", None)),
        )

    @property
    def owner(self) -> OwnerT:
        """The exact application object responsible for this action."""
        return self.ui.owner

    @property
    def interaction(self) -> discord.Interaction[discord.Client]:
        """The deliberate escape hatch to the native component interaction."""
        interaction = self._request.interaction
        assert interaction is not None
        return interaction

    @overload
    async def respond[ComponentT: Component[ComponentsV2Target]](
        self,
        content: ComponentT | Response[ComponentT],
        *,
        spec: ResponseSpec | None = None,
        files: Sequence[discord.File] = (),
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult[ComponentT]: ...

    @overload
    async def respond(
        self,
        content: FacadeContent | Response,
        *,
        spec: ResponseSpec | None = None,
        files: Sequence[discord.File] = (),
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult: ...

    async def respond(
        self,
        content: FacadeContent | Response,
        *,
        spec: ResponseSpec | None = None,
        files: Sequence[discord.File] = (),
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Respond through the same acknowledgement ledger as command requests."""
        return await self._request.respond(content, spec=spec, files=files, **overrides)

    async def defer(self, policy: Literal["private", "public"] | None = None) -> None:
        """Acknowledge this action before longer work."""
        await self._request.defer(policy)

    async def open_form(self, form: FormSpec | ModalSpec | discord.ui.Modal) -> None:
        """Open a portable or native-owned modal as this action's initial response."""
        await self._request.open_form(form)


__all__ = ["DiscordAction"]

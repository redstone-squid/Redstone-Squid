"""Owner-typed normalized request used by the Discord facade."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Unpack, cast, overload

import discord

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui.text import Localization
from squid_ui_discord.audience import Private, Visibility
from squid_ui_discord.contracts import FacadeContent, ResponseSource
from squid_ui_discord.delivery import (
    DeliveryAbandoned,
    DeliveryResult,
    MessageDestination,
    Replyable,
    handle_for,
    reply_to,
    respond_to,
    send_to,
)
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.response import Response, ResponseOverrides, ResponseResult, ResponseSpec

if TYPE_CHECKING:
    from squid_ui_discord.facade import DiscordUI
    from squid_ui_discord.runtime import DiscordUIRuntime


@dataclass(slots=True)
class DiscordRequest[OwnerT, SourceT: ResponseSource = ResponseSource]:
    """An owner-bound Discord event; its response authority ends with dispatch."""

    ui: DiscordUI[OwnerT]
    source: SourceT
    localization: Localization
    _user: discord.User | discord.Member
    _guild: discord.Guild | None
    _responses: int = 0

    @classmethod
    async def create[RequestOwnerT, RequestSourceT: ResponseSource](
        cls,
        ui: DiscordUI[RequestOwnerT],
        source: RequestSourceT,
    ) -> DiscordRequest[RequestOwnerT, RequestSourceT]:
        """Resolve localization and normalized identity exactly once."""
        localization = ui.runtime.defaults.localization
        if ui.runtime.localization is not None:
            localization = await ui.runtime.localization(source)
        user = getattr(source, "user", None) or getattr(source, "author", None)
        if user is None:
            message = "request source identifies no user"
            raise TypeError(message)
        guild = cast(discord.Guild | None, getattr(source, "guild", None))
        return cls(ui, source, localization, cast(discord.User | discord.Member, user), guild)

    @property
    def owner(self) -> OwnerT:
        """The exact application object responsible for this request."""
        return self.ui.owner

    @property
    def runtime(self) -> DiscordUIRuntime[discord.Client]:
        """The runtime backing this request's owner scope."""
        return self.ui.runtime

    @property
    def client(self) -> discord.Client:
        """The Discord client that owns this dispatch."""
        return self.ui.runtime.client

    @property
    def user(self) -> discord.User | discord.Member:
        """The actor normalized across interaction, context, and message sources."""
        return self._user

    @property
    def guild(self) -> discord.Guild | None:
        """The guild in which this request arrived, if any."""
        return self._guild

    @property
    def channel(self) -> discord.abc.Messageable | None:
        """The channel in which this request arrived, if any."""
        return cast(discord.abc.Messageable | None, getattr(self.source, "channel", None))

    @property
    def interaction(self) -> discord.Interaction[discord.Client] | None:
        """The native interaction, including one carried by a hybrid context."""
        if hasattr(self.source, "response") and hasattr(self.source, "followup"):
            return cast(discord.Interaction[discord.Client], self.source)
        return cast(discord.Interaction[discord.Client] | None, getattr(self.source, "interaction", None))

    @property
    def locale(self) -> str | None:
        """The resolved locale identifier."""
        return self.localization.locale

    @property
    def responded(self) -> bool:
        """Whether this request explicitly produced a facade response."""
        return self._responses > 0

    def destination(
        self,
        audience: Visibility,
        *,
        files: Sequence[discord.File],
        allowed_mentions: discord.AllowedMentions,
        complete_deferred: bool = False,
    ) -> MessageDestination:
        """Build the source-appropriate destination for one audience policy."""
        del complete_deferred
        if isinstance(audience, Private):
            return self._private_destination(audience, files=files, allowed_mentions=allowed_mentions)
        return self._ordinary_destination(
            personal=audience == "personal",
            files=files,
            allowed_mentions=allowed_mentions,
        )

    def _ordinary_destination(
        self,
        *,
        personal: bool,
        files: Sequence[discord.File],
        allowed_mentions: discord.AllowedMentions,
    ) -> MessageDestination:
        source = self.source
        if callable(getattr(source, "send", None)):
            context = cast(Replyable, source)
            ephemeral = personal and getattr(context, "interaction", None) is not None
            return reply_to(context, ephemeral=ephemeral, files=files, allowed_mentions=allowed_mentions)
        if hasattr(source, "response") and hasattr(source, "followup"):
            return respond_to(
                cast(discord.Interaction[discord.Client], source),
                ephemeral=personal,
                wait=True,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        message = cast(discord.Message, source)

        async def reply(payload: MessagePayload) -> DeliveryResult:
            delivered = await message.reply(
                files=[*files, *payload.build_files()],
                allowed_mentions=allowed_mentions,
                mention_author=False,
                **payload._send_fields(),
            )
            return DeliveryResult(delivered, handle_for(delivered, mode=payload.mode))

        return reply

    def _private_destination(
        self,
        audience: Private,
        *,
        files: Sequence[discord.File],
        allowed_mentions: discord.AllowedMentions,
    ) -> MessageDestination:
        source = self.source
        if hasattr(source, "response") and hasattr(source, "followup"):
            return respond_to(
                cast(discord.Interaction[discord.Client], source),
                ephemeral=True,
                wait=True,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        if callable(getattr(source, "send", None)) and (
            getattr(source, "interaction", None) is not None or self.guild is None
        ):
            return reply_to(cast(Replyable, source), ephemeral=True, files=files, allowed_mentions=allowed_mentions)
        if isinstance(source, discord.Message) and self.guild is None:
            return self._ordinary_destination(personal=False, files=files, allowed_mentions=allowed_mentions)

        private = send_to(self.user, files=files, allowed_mentions=allowed_mentions)
        public = self._ordinary_destination(personal=False, files=(), allowed_mentions=allowed_mentions)

        async def deliver(payload: MessagePayload) -> DeliveryResult:
            from squid_ui import paragraph

            try:
                result = await private(payload)
            except discord.Forbidden:
                notice = self.ui.render(
                    paragraph(self.runtime.defaults.chrome.dm_unavailable),
                    localization=self.localization,
                )
                await public(notice)
                raise DeliveryAbandoned from None
            confirmation = self.ui.render(
                [
                    paragraph(self.runtime.defaults.chrome.sent_privately),
                    paragraph(audience.reason),
                ],
                localization=self.localization,
            )
            await public(confirmation)
            return result

        return deliver

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
        """Respond through this request's already-resolved identity and localization."""
        result = await self.ui._respond_resolved(
            self,
            content,
            spec=spec,
            overrides=overrides,
            files=files,
        )
        self._responses += 1
        return result


__all__ = ["DiscordRequest"]

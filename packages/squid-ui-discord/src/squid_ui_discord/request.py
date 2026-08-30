"""Owner-typed normalized request used by the Discord facade."""

from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Unpack, cast, overload

import discord

from squid_ui.forms import FormSpec
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
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.modal import ModalSpec, build_form_modal, build_modal
from squid_ui_discord.response import UNSET, Response, ResponseOverrides, ResponseResult, ResponseSpec

if TYPE_CHECKING:
    from squid_ui_discord.facade import DiscordUI
    from squid_ui_discord.runtime import DiscordUIRuntime

type AcknowledgementPolicy = Literal["none", "private", "public", "form"]


@dataclass(slots=True)
class DiscordRequest[OwnerT, SourceT: ResponseSource = ResponseSource]:
    """An owner-bound Discord event; its response authority ends with dispatch."""

    ui: DiscordUI[OwnerT]
    source: SourceT
    localization: Localization
    _user: discord.User | discord.Member
    _guild: discord.Guild | None
    acknowledgement: AcknowledgementPolicy = "none"
    _responses: int = 0
    _deferred: Literal["private", "public"] | None = None
    _form_opened: bool = False

    @classmethod
    async def create[RequestOwnerT, RequestSourceT: ResponseSource](
        cls,
        ui: DiscordUI[RequestOwnerT],
        source: RequestSourceT,
        *,
        acknowledgement: AcknowledgementPolicy = "none",
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
        return cls(ui, source, localization, cast(discord.User | discord.Member, user), guild, acknowledgement)

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
        if isinstance(audience, Private):
            if complete_deferred:
                return respond_to(
                    cast(discord.Interaction[discord.Client], self.interaction),
                    ephemeral=True,
                    wait=True,
                    files=files,
                    allowed_mentions=allowed_mentions,
                    complete_deferred=True,
                )
            return self._private_destination(audience, files=files, allowed_mentions=allowed_mentions)
        if complete_deferred:
            return respond_to(
                cast(discord.Interaction[discord.Client], self.interaction),
                ephemeral=audience == "personal",
                wait=True,
                files=files,
                allowed_mentions=allowed_mentions,
                complete_deferred=True,
            )
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
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult[ComponentT]: ...

    @overload
    async def respond(
        self,
        content: FacadeContent | Response,
        *,
        spec: ResponseSpec | None = None,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult: ...

    async def respond(
        self,
        content: FacadeContent | Response,
        *,
        spec: ResponseSpec | None = None,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Respond safely, completing a managed defer before creating follow-ups."""
        if self._form_opened:
            message = "a form response cannot be followed by message content in the same dispatch"
            raise RuntimeError(message)
        policy_content = content.content if isinstance(content, Response) else content
        if hasattr(type(policy_content), "__response_spec__"):
            if policy_content.__dict__.get("_screen_presented", False):
                message = f"{type(policy_content).__name__} has already been presented"
                raise RuntimeError(message)
            object.__setattr__(policy_content, "_screen_opening", self)
            object.__setattr__(policy_content, "_screen_presented", True)
        explicit = self._explicit_audience(content, spec, overrides)
        if self._deferred is not None:
            compatible = explicit in (None, "personal") or isinstance(explicit, Private)
            if self._deferred == "public":
                compatible = explicit in (None, "public")
            if not compatible:
                message = "response audience conflicts with the managed defer"
                raise RuntimeError(message)
            if explicit is None:
                overrides = {**overrides, "audience": "personal" if self._deferred == "private" else "public"}
        interaction = self.interaction
        complete_deferred = self._responses == 0 and self._deferred is not None
        if interaction is not None and self._responses == 0 and interaction.response.is_done():
            response_type = interaction.response.type
            if self._deferred is None and response_type is discord.InteractionResponseType.deferred_channel_message:
                message = "the interaction was deferred outside this request ledger"
                raise RuntimeError(message)
        result = await self.ui._respond_resolved(
            self,
            content,
            spec=spec,
            overrides=overrides,
            files=files,
            complete_deferred=complete_deferred,
            parent=parent,
            session_key=session_key,
        )
        self._responses += 1
        return result

    @staticmethod
    def _explicit_audience(
        content: FacadeContent | Response,
        spec: ResponseSpec | None,
        overrides: ResponseOverrides,
    ) -> Visibility | None:
        explicit = overrides.get("audience")
        if explicit is not None:
            return explicit
        if spec is not None and spec.audience is not UNSET:
            return cast(Visibility, spec.audience)
        if isinstance(content, Response):
            if content.overrides is not None and "audience" in content.overrides:
                return content.overrides["audience"]
            if content.spec is not None and content.spec.audience is not UNSET:
                return cast(Visibility, content.spec.audience)
        return None

    async def defer(self, policy: Literal["private", "public"] | None = None) -> None:
        """Acknowledge this request; the first later response completes it."""
        if self.acknowledgement == "form":
            message = "a form-reserved handler cannot defer a message response"
            raise RuntimeError(message)
        interaction = self.interaction
        if interaction is None:
            return
        if interaction.response.is_done():
            if self._deferred is None:
                message = "the interaction was already acknowledged outside this request ledger"
                raise RuntimeError(message)
            return
        selected = policy
        if selected is None:
            selected = "public" if self.acknowledgement == "public" else "private"
        await interaction.response.defer(ephemeral=selected == "private", thinking=True)
        self._deferred = selected

    async def open_form(
        self,
        form: FormSpec | ModalSpec | discord.ui.Modal,
        *,
        on_submit: Callable[[discord.Interaction[discord.Client], dict[str, object]], Awaitable[None]] | None = None,
    ) -> None:
        """Open a declarative or native-owned modal as the initial acknowledgement."""
        interaction = self.interaction
        if interaction is None:
            message = "forms require an interaction request"
            raise TypeError(message)
        if interaction.response.is_done() or self._responses:
            message = "a form must be the interaction's initial response"
            raise RuntimeError(message)
        if isinstance(form, discord.ui.Modal):
            modal = form
        elif isinstance(form, ModalSpec):
            modal = build_modal(form)
        else:

            async def submit(current: discord.Interaction[discord.Client], values: dict[str, object]) -> None:
                if on_submit is not None:
                    await on_submit(current, values)

            modal = build_form_modal(form, on_submit=submit, localization=self.localization)
        await interaction.response.send_modal(modal)
        self._form_opened = True


__all__ = ["AcknowledgementPolicy", "DiscordRequest"]

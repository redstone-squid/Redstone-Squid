"""One localized Discord invocation and its complete UI delivery policy."""

from collections.abc import Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Unpack, cast

import discord

from squid_ui import ComponentsV2Target, LayoutNode, paragraph
from squid_ui.runtime.component import Component
from squid_ui.text import Localization, TextLike, resolve_text
from squid_ui_discord._invocation_context import current_cell
from squid_ui_discord._invocation_context import invocation_scope as _invocation_scope
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.delivery import (
    DeliveryAbandoned,
    DeliveryResult,
    MessageDestination,
    Replyable,
    reply_to,
    respond_to,
    send_to,
)
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import MessageRootBehaviorOptions
from squid_ui_discord.rendering import render_static
from squid_ui_discord.runtime import ClientRuntime, InvocationSource
from squid_ui_discord.session_specs import OpenContext, SessionOptions, SessionSpec
from squid_ui_discord.sessions import OpenResult, Rejected


@dataclass(frozen=True, slots=True)
class Private:
    """Deliver where a guild channel can never see the payload."""

    reason: TextLike


type Visibility = Literal["public", "personal"] | Private


def _is_interaction(source: InvocationSource) -> bool:
    return hasattr(source, "response") and hasattr(source, "followup")


def _is_context(source: InvocationSource) -> bool:
    return callable(getattr(source, "send", None))


def _identity(source: InvocationSource) -> tuple[discord.User | discord.Member, discord.Guild | None]:
    user = getattr(source, "user", None)
    if user is None:
        user = getattr(source, "author", None)
    if user is None:
        message = "invocation source names neither a user nor an author"
        raise TypeError(message)
    return cast(discord.User | discord.Member, user), cast(discord.Guild | None, getattr(source, "guild", None))


@dataclass(frozen=True, slots=True, init=False)
class Invocation:
    """A Discord event with its installed runtime, identity, and resolved localization."""

    source: InvocationSource
    runtime: ClientRuntime[Any]
    localization: Localization
    user: discord.User | discord.Member
    guild: discord.Guild | None

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        message = "Invocation is resolved asynchronously; use await Invocation.of(source)"
        raise TypeError(message)

    @property
    def client(self) -> discord.Client:
        """The client on which this invocation's runtime is installed."""
        return self.runtime.client

    @classmethod
    async def of(cls, source: InvocationSource) -> Invocation:
        """Resolve an invocation, reusing the ambient handler's lazy memo when present."""
        cell = current_cell()
        if cell is not None and cell.source is source:
            if cell.value is not None:
                return cell.value
            async with cell.lock:
                if cell.value is None:
                    cell.value = await cls._resolve(source)
            return cell.value
        return await cls._resolve(source)

    @classmethod
    async def _resolve(cls, source: InvocationSource) -> Invocation:
        runtime = ClientRuntime.of(source)
        localization = runtime.defaults.localization
        if runtime.localization is not None:
            localization = await runtime.localization(source)
        user, guild = _identity(source)
        invocation = object.__new__(cls)
        object.__setattr__(invocation, "source", source)
        object.__setattr__(invocation, "runtime", runtime)
        object.__setattr__(invocation, "localization", localization)
        object.__setattr__(invocation, "user", user)
        object.__setattr__(invocation, "guild", guild)
        return invocation

    def t(self, message: TextLike) -> str:
        """Resolve deferred text immediately for a surface outside the layout system."""
        return resolve_text(message, self.localization).content

    def render(self, *nodes: LayoutNode[ComponentsV2Target]) -> MessagePayload:
        """Render nodes through the installed host defaults and invocation localization."""
        defaults = self.runtime.defaults
        return render_static(
            nodes,
            target=defaults.target,
            chrome=defaults.chrome,
            localization=self.localization,
            palette=defaults.palette,
            strict=defaults.strict,
        )

    def destination(
        self,
        visibility: Visibility = "public",
        *,
        wait: bool = False,
        files: Sequence[discord.File] = (),
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> MessageDestination:
        """Build the source-appropriate destination for one audience policy."""
        if isinstance(visibility, Private):
            return self._private_destination(
                visibility,
                wait=wait,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        return self._ordinary_destination(
            personal=visibility == "personal",
            wait=wait,
            files=files,
            allowed_mentions=allowed_mentions,
        )

    async def reply(
        self,
        *nodes: LayoutNode[ComponentsV2Target],
        visibility: Visibility = "public",
        wait: bool = False,
        files: Sequence[discord.File] = (),
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> DeliveryResult:
        """Render and answer this invocation through its audience policy."""
        return await self.destination(
            visibility,
            wait=wait,
            files=files,
            allowed_mentions=allowed_mentions,
        )(self.render(*nodes))

    async def mount(
        self,
        component: Component[ComponentsV2Target],
        *,
        access: AccessPolicy,
        visibility: Visibility = "public",
        wait: bool = False,
        **options: Unpack[MessageRootBehaviorOptions],
    ) -> MessageRoot:
        """Construct and deliver a plain message root through this invocation."""
        configured = cast(MessageRootBehaviorOptions, {**options, "localization": self.localization})
        message_root = self.runtime.mount(component, access=access, **configured)
        await message_root.send(self.destination(visibility, wait=wait))
        return message_root

    async def open(
        self,
        component: Component[Any],
        spec: SessionSpec,
        *,
        visibility: Visibility = "public",
        parent: MessageRoot | None = None,
        wait: bool = False,
        key: Hashable | None = None,
        **options: Unpack[SessionOptions],
    ) -> OpenResult:
        """Open or attach a session screen, rendering any policy-authored rejection notice."""
        open_context = OpenContext.of(self.source)
        destination = self.destination(visibility, wait=wait)
        configured = cast(SessionOptions, {**options, "localization": self.localization})
        if parent is None:
            result = await spec.open(
                component,
                destination,
                sessions=self.runtime.sessions,
                open_context=open_context,
                key=key,
                **configured,
            )
        else:
            result = await spec.attach(
                component,
                destination,
                sessions=self.runtime.sessions,
                open_context=open_context,
                parent=parent,
                **configured,
            )
        if isinstance(result, Rejected) and result.notice is not None:
            await self.reply(paragraph(result.notice), visibility=visibility, wait=wait)
        return result

    def _ordinary_destination(
        self,
        *,
        personal: bool,
        wait: bool,
        files: Sequence[discord.File],
        allowed_mentions: discord.AllowedMentions | None,
    ) -> MessageDestination:
        source = self.source
        if _is_context(source):
            context = cast(Replyable, source)
            interaction = getattr(context, "interaction", None)
            ephemeral = personal and interaction is not None
            return reply_to(context, ephemeral=ephemeral, files=files, allowed_mentions=allowed_mentions)
        if _is_interaction(source):
            return respond_to(
                cast(discord.Interaction[Any], source),
                ephemeral=personal,
                wait=wait,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        channel = getattr(source, "channel", None)
        if channel is None:
            message = "message invocation source has no channel"
            raise TypeError(message)
        return send_to(channel, files=files, allowed_mentions=allowed_mentions)

    def _private_destination(
        self,
        visibility: Private,
        *,
        wait: bool,
        files: Sequence[discord.File],
        allowed_mentions: discord.AllowedMentions | None,
    ) -> MessageDestination:
        source = self.source
        if _is_interaction(source):
            return respond_to(
                cast(discord.Interaction[Any], source),
                ephemeral=True,
                wait=wait,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        if _is_context(source) and (getattr(source, "interaction", None) is not None or self.guild is None):
            return reply_to(cast(Replyable, source), ephemeral=True, files=files, allowed_mentions=allowed_mentions)
        if not _is_context(source) and self.guild is None:
            channel = getattr(source, "channel", None)
            if channel is None:
                message = "message invocation source has no channel"
                raise TypeError(message)
            return send_to(channel, files=files, allowed_mentions=allowed_mentions)

        private = send_to(self.user, files=files, allowed_mentions=allowed_mentions)
        public = self._ordinary_destination(personal=False, wait=wait, files=(), allowed_mentions=None)

        async def deliver(payload: MessagePayload) -> DeliveryResult:
            try:
                result = await private(payload)
            except discord.Forbidden:
                notice = self.render(paragraph(self.runtime.defaults.chrome.dm_unavailable))
                await public(notice)
                raise DeliveryAbandoned from None
            confirmation = self.render(
                paragraph(self.runtime.defaults.chrome.sent_privately),
                paragraph(visibility.reason),
            )
            await public(confirmation)
            return result

        return deliver


def current_invocation() -> Invocation | None:
    """Return the resolved ambient invocation, or `None` before its first use or outside a scope."""
    cell = current_cell()
    return None if cell is None else cell.value


@contextmanager
def invocation_scope(source: InvocationSource) -> Iterator[None]:
    """Establish a lazy invocation memo for the duration of one handler dispatch."""
    with _invocation_scope(source):
        yield


__all__ = ["Invocation", "Private", "Visibility", "current_invocation", "invocation_scope"]

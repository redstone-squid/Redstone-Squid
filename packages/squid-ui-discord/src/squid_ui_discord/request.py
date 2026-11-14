"""Give commands and component callbacks one interface for replies and acknowledgement.

A Request brings together the invoking user, locale, application-owner scope, and native
Discord source. Its response ledger records managed deferral, delivered responses, and
modal opening. Helpers resolving the same source share that record, so the first reply
can complete a deferral and later replies use follow-ups without acknowledging twice.

Requests are cached on source extras, or in a weak-key fallback when supported.
Sources that support neither mechanism receive a fresh request on each resolution.
"""

import contextlib
import weakref
from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Unpack, cast, overload

import discord
from discord import app_commands
from discord.ext import commands

from squid_ui import paragraph
from squid_ui.forms import FormSpec
from squid_ui.interactions import ActionEvent
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui.text import Localization
from squid_ui_discord.actions import ActionResponder
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
from squid_ui_discord.response import Response, ResponseOverrides, ResponseResult
from squid_ui_discord.sessions import Session

if TYPE_CHECKING:
    from squid_ui_discord.facade import Scope
    from squid_ui_discord.runtime import DiscordUIRuntime

type Deferral = Literal["private", "public"]
type SourceKind = Literal["interaction", "context", "message"]
type RequestOrigin = ResponseSource | ActionEvent
"""Anything a request can be resolved from: a native source, or a component event."""

_MEMO_KEY = "squid_ui_discord.request"
_MEMO: weakref.WeakKeyDictionary[Any, Request[Any, Any]] = weakref.WeakKeyDictionary()
"""Requests for sources without an `extras` mapping (command contexts, messages, test doubles)."""


def _kind(source: object) -> SourceKind:
    """Classify a native source once, so nothing downstream sniffs attributes again.

    Duck-typed rather than isinstance-dispatched so gateway-free test doubles count.
    """
    if hasattr(source, "response") and hasattr(source, "followup"):
        return "interaction"
    if callable(getattr(source, "send", None)) and hasattr(source, "author"):
        return "context"
    if isinstance(source, discord.Message):
        return "message"
    message = f"{type(source).__name__} is not an interaction, command context, or message"
    raise TypeError(message)


def _memo_get(source: object) -> Request[Any, Any] | None:
    extras = getattr(source, "extras", None)
    if isinstance(extras, dict):
        found = extras.get(_MEMO_KEY)
        return found if isinstance(found, Request) else None
    try:
        return _MEMO.get(source)
    except TypeError:
        return None


def _memo_set(source: object, request: Request[Any, Any]) -> None:
    extras = getattr(source, "extras", None)
    if isinstance(extras, dict):
        extras[_MEMO_KEY] = request
        return
    # Not weak-referenceable: the caller keeps the request it was handed.
    with contextlib.suppress(TypeError):
        _MEMO[source] = request


@dataclass(slots=True, eq=False)
class Request[OwnerT = Any, ClientT: discord.Client = discord.Client]:
    """One Discord source, its resolved owner and locale, and its facade response ledger.

    The native source determines how long delivery credentials remain usable. Native sends
    made outside this facade do not update the ledger.
    """

    _runtime: DiscordUIRuntime[ClientT]
    _scope: Scope[OwnerT, ClientT]
    source: ResponseSource
    kind: SourceKind
    localization: Localization
    _user: discord.User | discord.Member
    _guild: discord.Guild | None
    root: MessageRoot | None = None
    """The message root whose control raised this request, when it came from a click."""
    _responses: int = 0
    _deferred: Deferral | None = None
    _form_opened: bool = False
    _audience: Visibility | None = None

    @property
    def runtime(self) -> DiscordUIRuntime[ClientT]:
        return self._runtime

    @property
    def scope(self) -> Scope[OwnerT, ClientT]:
        return self._scope

    @property
    def owner(self) -> OwnerT:
        """The application object whose scope tracks what this request opens."""
        return self.scope.owner

    @property
    def client(self) -> ClientT:
        """The Discord client that owns this dispatch."""
        return self.runtime.client

    @property
    def user(self) -> discord.User | discord.Member:
        """The actor, whichever kind of source named them."""
        return self._user

    @property
    def guild(self) -> discord.Guild | None:
        """The guild this request arrived in, if any."""
        return self._guild

    @property
    def channel(self) -> discord.abc.Messageable | None:
        """The channel this request arrived in, if any."""
        return cast(discord.abc.Messageable | None, getattr(self.source, "channel", None))

    @property
    def interaction(self) -> discord.Interaction[ClientT] | None:
        """The native interaction, including one carried by a hybrid context."""
        if self.kind == "interaction":
            return cast(discord.Interaction[ClientT], self.source)
        if self.kind == "context":
            return cast(discord.Interaction[ClientT] | None, getattr(self.source, "interaction", None))
        return None

    @property
    def context(self) -> Replyable | None:
        """The prefix or hybrid command context, when that is what arrived."""
        return cast(Replyable, self.source) if self.kind == "context" else None

    @property
    def message(self) -> discord.Message | None:
        """The message, when a bare message is what arrived."""
        return cast(discord.Message, self.source) if self.kind == "message" else None

    @property
    def command(self) -> Any:
        """The discord.py command being invoked, if the source names one."""
        return getattr(self.source, "command", None)

    @property
    def session(self) -> Session | None:
        """The session the raising control's root belongs to, for click-origin requests."""
        return None if self.root is None else self.runtime.sessions.session_for(self.root)

    @property
    def locale(self) -> str | None:
        """The resolved locale identifier."""
        return self.localization.locale

    @property
    def responded(self) -> bool:
        """Whether this request has produced a response through the facade."""
        return self._responses > 0

    @property
    def deferred(self) -> Deferral | None:
        """How this request was acknowledged, which fixes the audience of what follows."""
        return self._deferred

    @property
    def audience(self) -> Visibility | None:
        """The audience fixed by deferral or the first delivered response, if any."""
        if self._deferred is not None:
            return "personal" if self._deferred == "private" else "public"
        return self._audience

    def destination(
        self,
        audience: Visibility,
        *,
        files: Sequence[discord.File] = (),
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> MessageDestination:
        """Build the source-appropriate destination for one audience, honouring the ledger.

        The first delivery after a managed defer completes it; later ones are follow-ups.
        """
        mentions = discord.AllowedMentions.none() if allowed_mentions is None else allowed_mentions
        if self._responses == 0 and self._deferred is not None:
            inner = respond_to(
                cast(discord.Interaction[discord.Client], self.interaction),
                ephemeral=isinstance(audience, Private) or audience == "personal",
                wait=True,
                files=files,
                allowed_mentions=mentions,
                complete_deferred=True,
            )
        elif isinstance(audience, Private):
            inner = self._private_destination(audience, files=files, allowed_mentions=mentions)
        else:
            inner = self._ordinary_destination(personal=audience == "personal", files=files, allowed_mentions=mentions)

        async def deliver(payload: MessagePayload) -> DeliveryResult:
            result = await inner(payload)
            if self._responses == 0:
                self._audience = audience
            self._responses += 1
            return result

        return deliver

    def _ordinary_destination(
        self,
        *,
        personal: bool,
        files: Sequence[discord.File],
        allowed_mentions: discord.AllowedMentions,
    ) -> MessageDestination:
        if self.kind == "context":
            context = cast(Replyable, self.source)
            ephemeral = personal and getattr(context, "interaction", None) is not None
            return reply_to(context, ephemeral=ephemeral, files=files, allowed_mentions=allowed_mentions)
        if self.kind == "interaction":
            return respond_to(
                cast(discord.Interaction[discord.Client], self.source),
                ephemeral=personal,
                wait=True,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        message = cast(discord.Message, self.source)

        async def reply(payload: MessagePayload) -> DeliveryResult:
            delivered = await message.reply(
                files=[*files, *await payload.prepare_files()],
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
        if self.kind == "interaction":
            return respond_to(
                cast(discord.Interaction[discord.Client], self.source),
                ephemeral=True,
                wait=True,
                files=files,
                allowed_mentions=allowed_mentions,
            )
        if self.kind == "context" and (getattr(self.source, "interaction", None) is not None or self.guild is None):
            return reply_to(
                cast(Replyable, self.source), ephemeral=True, files=files, allowed_mentions=allowed_mentions
            )
        if self.kind == "message" and self.guild is None:
            return self._ordinary_destination(personal=False, files=files, allowed_mentions=allowed_mentions)

        private = send_to(self.user, files=files, allowed_mentions=allowed_mentions)
        public = self._ordinary_destination(personal=False, files=(), allowed_mentions=allowed_mentions)

        async def deliver(payload: MessagePayload) -> DeliveryResult:
            try:
                result = await private(payload)
            except discord.Forbidden:
                notice = self.scope.render(
                    paragraph(self.runtime.defaults.chrome.dm_unavailable),
                    localization=self.localization,
                )
                await public(notice)
                raise DeliveryAbandoned from None
            confirmation = self.scope.render(
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
    async def respond[ComponentT: Component[ComponentsV2Target, Request[Any, Any]]](
        self,
        content: ComponentT | Response[ComponentT],
        *,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult[ComponentT]: ...

    @overload
    async def respond(
        self,
        content: FacadeContent[ClientT] | Response,
        *,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult: ...

    # BasedPyright infers `Presented`'s parameter from a frozen dataclass field as if the field
    # were writable, so `ResponseResult[ComponentT]` is not a `ResponseResult`. The first overload
    # is the reason this ladder exists: it is what gives a component response its own type back.
    async def respond(  # pyright: ignore[reportInconsistentOverload]
        self,
        content: FacadeContent[ClientT] | Response[Any],
        *,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Present content through this request's scope and delivery policy.

        A managed defer fixes the audience for this request; an incompatible explicit
        audience raises. Each Screen instance may be presented only once, including attempts
        that fail before delivery, so construct a fresh instance when retrying.
        """
        if self._form_opened:
            message = "a form response cannot be followed by message content in the same dispatch"
            raise RuntimeError(message)
        resolved: FacadeContent[ClientT]
        if isinstance(content, Response):
            overrides = {**content._options, **overrides}
            resolved = content.content
        else:
            resolved = content
        explicit = overrides.get("audience")
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
        if interaction is not None and self._responses == 0 and interaction.response.is_done():
            response_type = interaction.response.type
            if self._deferred is None and response_type is discord.InteractionResponseType.deferred_channel_message:
                message = "the interaction was deferred outside this request ledger"
                raise RuntimeError(message)
        return await self.scope._respond_resolved(
            self,
            resolved,
            overrides=overrides,
            files=files,
            parent=parent,
            session_key=session_key,
        )

    async def defer(self, policy: Deferral = "private") -> None:
        """Acknowledge an interaction so the first later response completes it.

        Non-interaction sources are unchanged. Repeating a managed defer keeps its original
        policy; a response already acknowledged outside this request cannot be deferred here.
        """
        interaction = self.interaction
        if interaction is None:
            return
        if interaction.response.is_done():
            if self._deferred is None:
                message = "the interaction was already acknowledged outside this request ledger"
                raise RuntimeError(message)
            return
        await interaction.response.defer(ephemeral=policy == "private", thinking=True)
        self._deferred = policy

    async def form(
        self,
        form: FormSpec | ModalSpec | discord.ui.Modal,
        *,
        on_submit: Callable[[discord.Interaction[discord.Client], dict[str, object]], Awaitable[None]] | None = None,
    ) -> None:
        """Open a modal as the initial acknowledgement, before deferring or responding.

        For a FormSpec, ``on_submit`` receives raw values read from the native controls;
        callers own schema evaluation and validation. ModalSpec and native Modal callbacks
        remain responsible for their own submission handling.
        """
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


@overload
async def request[ClientT: discord.Client](source: discord.Interaction[ClientT]) -> Request[Any, ClientT]: ...


@overload
async def request[BotT: commands.Bot](source: commands.Context[BotT]) -> Request[Any, BotT]: ...


@overload
async def request(source: RequestOrigin) -> Request[Any]: ...


async def request(source: RequestOrigin) -> Request[Any, Any]:
    """Resolve the one request for a source, inferring its owner from Discord dispatch.

    Commands inherit their binding's registered scope; component events inherit their root's
    scope. Unbound sources use the application scope until an owner claims them before a
    response. For listeners without a command binding, use `self.ui.request(source)`.
    Missing or closed command-owner scopes raise instead of falling back to the application.
    """
    return await _resolve_request(source)


def _command_binding(source: object) -> object | None:
    """Read only discord.py command objects; arbitrary source attributes are not ownership."""
    command = getattr(source, "command", None)
    if isinstance(command, commands.Command):
        return command.cog
    if isinstance(command, app_commands.Command):
        return command.binding
    return None


def _binding_scope(runtime: DiscordUIRuntime[Any], binding: object) -> Scope[Any, Any]:
    # Class-body groups organize commands but do not own panel lifetimes.
    return runtime.app if isinstance(binding, app_commands.Group) else runtime.scope_for(binding)


async def _request_for_binding(source: RequestOrigin, binding: object | None) -> Request[Any, Any]:
    """Apply the wrapper's actual binding, including context-menu and autocomplete callbacks."""
    # Runtime constructs scopes, whose request method imports this resolver.
    from squid_ui_discord.runtime import DiscordUIRuntime

    runtime = DiscordUIRuntime.of(cast(Any, source))
    scope = runtime.app if binding is None else _binding_scope(runtime, binding)
    return await _resolve_request(source, scope=scope)


@overload
async def _resolve_request[OwnerT, ClientT: discord.Client](
    source: RequestOrigin, *, scope: Scope[OwnerT, ClientT], root: MessageRoot | None = None
) -> Request[OwnerT, ClientT]: ...


@overload
async def _resolve_request(
    source: RequestOrigin, *, scope: None = None, root: MessageRoot | None = None
) -> Request[Any, Any]: ...


async def _resolve_request(
    source: RequestOrigin, *, scope: Scope[Any, Any] | None = None, root: MessageRoot | None = None
) -> Request[Any, Any]:
    """Share source normalization and memoization between inferred and bound resolution."""
    # Runtime imports Scope, and Scope imports this module.
    from squid_ui_discord.runtime import DiscordUIRuntime

    native: object = source
    localization: Localization | None = None if root is None else root.localization
    if isinstance(source, ActionEvent):
        responder = source.responder
        if not isinstance(responder, ActionResponder):
            frontend = source.metadata.get("frontend", type(responder).__name__)
            message = f"this event came from frontend {frontend!r}, not Discord"
            raise LookupError(message)  # noqa: TRY004
        native = responder.interaction
        root = responder.message_root
        localization = root.localization
    runtime = DiscordUIRuntime.of(cast(Any, native))
    if scope is not None:
        scope._require_live()
        if scope.runtime is not runtime:
            message = "the request source belongs to a different Discord runtime"
            raise ValueError(message)
    inferred: Scope[Any, Any] | None = None
    if root is not None:
        inferred = runtime.scope_of_root(root)
    elif (binding := _command_binding(native)) is not None:
        inferred = _binding_scope(runtime, binding)
    if scope is not None and inferred is not None and scope is not inferred:
        message = "the request source belongs to a different UI scope"
        raise ValueError(message)
    selected = scope if scope is not None else inferred
    found = _memo_get(native)
    if found is not None:
        found.scope._require_live()
        if selected is not None and found.scope is not selected:
            if found.scope is not runtime.app or found.responded or found._form_opened:
                message = "the request already belongs to a different UI scope"
                raise ValueError(message)
            found._scope = selected
        if root is not None and found.root is None:
            found.root = root
            found.localization = root.localization
        return found
    kind = _kind(native)
    if localization is None:
        localization = runtime.defaults.localization
        if runtime.localization is not None:
            localization = await runtime.localization(cast(Any, native))
    user = getattr(native, "user", None) if kind == "interaction" else getattr(native, "author", None)
    if user is None:
        message = "request source identifies no user"
        raise TypeError(message)
    selected = runtime.app if selected is None else selected
    selected._require_live()
    resolved = Request[Any, Any](
        runtime,
        selected,
        cast(ResponseSource, native),
        kind,
        localization,
        cast(discord.User | discord.Member, user),
        cast(discord.Guild | None, getattr(native, "guild", None)),
        root,
    )
    _memo_set(native, resolved)
    return resolved


__all__ = ["Deferral", "Request", "RequestOrigin", "SourceKind", "request"]

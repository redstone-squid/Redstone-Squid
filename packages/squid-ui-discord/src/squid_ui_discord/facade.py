"""Group live Discord panels under the application object that owns them.

A Scope belongs to one runtime and one owner, such as a cog. Request-based replies and
scheduled sends both use it to apply response defaults and track opened message roots.
Closing the scope finishes those roots and their sessions, so unloading an owner does
not leave interactive panels calling back into it.
"""

from collections.abc import Hashable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Unpack, cast

import anyio
import discord

from squid_ui import paragraph
from squid_ui.runtime.component import Component
from squid_ui.text import Localization
from squid_ui_discord.access import AccessPolicy, Owner
from squid_ui_discord.audience import Audience
from squid_ui_discord.closure import CloseReason
from squid_ui_discord.contracts import DocumentContent, FacadeContent, ResponseSource, SendDestination
from squid_ui_discord.delivery import Abandoned as DeliveryAbandoned
from squid_ui_discord.delivery import DeliveryResult, MessageDestination, edit_to, no_mentions, send_to
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import PauseUpdates, RenewEphemeral
from squid_ui_discord.rendering import render_static
from squid_ui_discord.request import Request, RequestOrigin, _resolve_request
from squid_ui_discord.response import (
    UNSET,
    Abandoned,
    Presented,
    Rejected,
    ResponseOverrides,
    ResponseResult,
    ResponseSpec,
    Sent,
    Setting,
    invoker_only,
)
from squid_ui_discord.screen import Screen
from squid_ui_discord.session_specs import OpenContext, SessionOptions
from squid_ui_discord.sessions import Opened, RejectionReason
from squid_ui_discord.sessions import Rejected as SessionRejected

if TYPE_CHECKING:
    from squid_ui_discord.runtime import DiscordUIRuntime


class Scope[OwnerT = Any]:
    """Apply response policies and own the lifetime of one application object's panels.

    A request carries the scope of the cog (or app) it was dispatched to, so replies go
    through `Request`. `send` and `edit` cover the no-request case: a scheduled post, an
    edit of a message nobody just clicked.

    Static content is rendered and delivered once. A Component opens a live message
    root, optionally managed by a session policy, and is tracked until the scope closes.
    Calling close finishes tracked roots and unregisters the scope.
    """

    def __init__(self, runtime: DiscordUIRuntime[discord.Client], owner: OwnerT) -> None:
        self._runtime = runtime
        self._owner = owner
        self._closed = False

    @property
    def runtime(self) -> DiscordUIRuntime[discord.Client]:
        """The process UI runtime that created this scope."""
        return self._runtime

    @property
    def owner(self) -> OwnerT:
        """The exact object whose lifetime owns this scope."""
        return self._owner

    @property
    def closed(self) -> bool:
        """Whether this scope has ended."""
        return self._closed

    def _require_live(self) -> None:
        if self._closed:
            message = "this Discord UI scope is closed"
            raise RuntimeError(message)

    def _policy(self, content: FacadeContent, overrides: ResponseOverrides) -> ResponseSpec:
        policy = self._runtime.response_defaults
        screen_spec = getattr(type(content), "__response_spec__", None)
        if isinstance(screen_spec, ResponseSpec):
            policy = policy.overlay(screen_spec)
        return policy.overlay(None, **overrides)

    async def request(self, source: RequestOrigin) -> Request[OwnerT]:
        """Resolve this owner's request, sharing the source's existing acknowledgement ledger.

        Use this in listeners and native callbacks whose source carries no command binding.
        A closed scope, a source from another runtime, or a request already owned elsewhere
        raises instead of opening a panel with a different lifetime.
        """
        self._require_live()
        return cast(Request[OwnerT], await _resolve_request(source, scope=self))

    def render(self, content: FacadeContent, *, localization: Localization | None = None) -> MessagePayload:
        """Render static content using the installed defaults.

        A Component is rendered once without loading, mounting, or starting its services.
        Native payloads, views, and embeds pass through without document planning or auditing.
        """
        defaults = self._runtime.defaults
        selected = defaults.localization if localization is None else localization
        if isinstance(content, MessagePayload):
            return content
        if isinstance(content, str):
            return MessagePayload.classic(content=content)
        if isinstance(content, discord.Embed):
            return MessagePayload.classic(embeds=(content,))
        if isinstance(content, discord.ui.LayoutView):
            return MessagePayload.components_v2(content)
        if isinstance(content, discord.ui.View):
            return MessagePayload.classic(view=content)
        rendered = content.render() if isinstance(content, Component) else content
        return render_static(
            cast(DocumentContent, rendered),
            target=defaults.target,
            chrome=defaults.chrome,
            localization=selected,
            palette=defaults.palette,
            strict=defaults.strict,
        )

    async def send(
        self,
        destination: SendDestination,
        content: FacadeContent,
        *,
        locale: str | None = None,
        files: Sequence[discord.File] = (),
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Send content to a concrete channel or DM destination.

        The resolved audience must be public, the default, because the destination already
        determines visibility. Live content requires an access policy that can be resolved
        without an opening actor; the default invoker-only policy cannot be used here.
        """
        self._require_live()
        policy = self._policy(content, overrides)
        audience = self._setting(policy.audience, default="public")
        if audience != "public":
            message = "out-of-band send requires a concrete destination and public audience policy"
            raise TypeError(message)
        localization = self._runtime.defaults.localization if locale is None else Localization(locale=locale)
        return await self._present(
            content,
            destination=send_to(destination, files=files, allowed_mentions=self._mentions(policy)),
            policy=policy,
            localization=localization,
            source=None,
            actor_id=None,
        )

    async def edit(
        self,
        target: discord.Message,
        content: FacadeContent,
        *,
        files: Sequence[discord.File] = (),
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Replace a Discord message with static or newly scoped live content."""
        self._require_live()
        if "audience" in overrides:
            message = "editing an existing message cannot change its audience"
            raise TypeError(message)
        policy = self._policy(content, overrides)
        return await self._present(
            content,
            destination=edit_to(target, files=files, allowed_mentions=self._mentions(policy)),
            policy=policy,
            localization=self._runtime.defaults.localization,
            source=None,
            actor_id=None,
        )

    async def _respond_resolved(
        self,
        request: Request[Any],
        content: FacadeContent,
        *,
        overrides: ResponseOverrides,
        files: Sequence[discord.File],
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
    ) -> ResponseResult:
        self._require_live()
        policy = self._policy(content, overrides)
        audience: Audience = self._setting(policy.audience, default="public")
        destination = request.destination(audience, files=files, allowed_mentions=self._mentions(policy))
        return await self._present(
            content,
            destination=destination,
            policy=policy,
            localization=request.localization,
            source=request.source,
            actor_id=request.user.id,
            parent=parent,
            session_key=session_key,
            opening=request,
        )

    async def _present(
        self,
        content: FacadeContent,
        *,
        destination: MessageDestination,
        policy: ResponseSpec,
        localization: Localization,
        source: ResponseSource | None,
        actor_id: int | None,
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        opening: Request[Any] | None = None,
    ) -> ResponseResult:
        screen = content if isinstance(content, Screen) else None
        if screen is not None:
            screen._screen_claim(self, opening)
        try:
            result = await self._deliver(
                content,
                destination=destination,
                policy=policy,
                localization=localization,
                source=source,
                actor_id=actor_id,
                parent=parent,
                session_key=session_key,
            )
            if screen is not None:
                if isinstance(result, Presented):
                    screen._screen_opened()
                else:
                    await screen._screen_opening_failed()
        except BaseException as error:
            if screen is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await screen._screen_opening_failed()
                    except BaseException as cleanup_error:
                        message = "screen presentation and cleanup failed"
                        raise BaseExceptionGroup(message, [error, cleanup_error]) from None
            raise
        return result

    async def _deliver(
        self,
        content: FacadeContent,
        *,
        destination: MessageDestination,
        policy: ResponseSpec,
        localization: Localization,
        source: ResponseSource | None,
        actor_id: int | None,
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
    ) -> ResponseResult:
        if parent is not None and session_key is not None:
            message = "parent= attaches to an existing session and cannot be combined with session_key="
            raise TypeError(message)
        if not isinstance(content, Component):
            if parent is not None or session_key is not None:
                message = "session attachment and keys require live component content"
                raise TypeError(message)
            return Sent(await destination(self.render(content, localization=localization)))
        access = self._access(policy, actor_id)
        options = self._root_options(policy, localization)
        session_spec = self._setting(policy.session, default=None)
        captured: DeliveryResult | None = None

        async def capture(payload: MessagePayload) -> DeliveryResult:
            nonlocal captured
            captured = await destination(payload)
            return captured

        if session_spec is None:
            if parent is not None or session_key is not None:
                message = "parent= and session_key= require a response session policy"
                raise TypeError(message)
            root = self._runtime.mount(content, access=access, **options)
            if isinstance(content, Screen):
                content._screen_bind(root, None)
            result = await root.send(capture)
            if isinstance(result, DeliveryAbandoned):
                return Abandoned()
            self._runtime._track(self, root)
            assert captured is not None
            return Presented(content, root, None, captured)
        if source is None:
            message = "session delivery requires a source identifying its actor and guild"
            raise TypeError(message)
        open_context = OpenContext.of(source)
        configured = replace(session_spec, access=lambda _context: access)
        options = await configured._message_root_options(open_context, options)
        root = self._runtime.mount(content, access=access, **options)
        parent_session = self._runtime.sessions.session_for(parent) if parent is not None else None
        if isinstance(content, Screen):
            content._screen_bind(root, parent_session)
        if parent is None:
            result = await self._runtime.sessions.open(
                root,
                capture,
                key=configured.key(open_context) if session_key is None else session_key,
                admission=configured.admission,
                actor_id=actor_id,
                capacity=configured.capacity,
                quota=configured.quota,
                domain=configured.domain,
            )
        elif parent_session is None:
            return Rejected(RejectionReason.SESSION_FINISHED)
        else:
            result = await parent_session.attach(root, capture, actor_id=actor_id, parent=parent)
        if isinstance(result, SessionRejected):
            notice_delivery = None
            if result.notice is not None:
                notice_delivery = await destination(self.render(paragraph(result.notice), localization=localization))
            return Rejected(result.reason, notice_delivery)
        if isinstance(result, DeliveryAbandoned):
            return Abandoned()
        assert isinstance(result, Opened) and captured is not None
        self._runtime._track(self, root)
        return Presented(content, root, result.session, captured)

    def _root_options(self, policy: ResponseSpec, localization: Localization) -> SessionOptions:
        options: SessionOptions = {}
        chrome = self._setting(policy.chrome, default=None)
        if chrome is not None:
            options["chrome"] = chrome
        options["retain_routed_on_timeout"] = self._setting(policy.retain_routed_on_timeout, default=False)
        options["timeout"] = self._setting(policy.timeout, default=180)
        options["localization"] = localization
        scheduler = self._runtime.scheduler if self._setting(policy.live_updates, default=False) else None
        options["scheduler"] = scheduler
        expiry = self._setting(policy.expiry, default=None)
        if expiry is not None:
            if isinstance(expiry, RenewEphemeral) and scheduler is None:
                expiry = PauseUpdates(expiry.warning)
            options["expiry"] = expiry
        return options

    @staticmethod
    def _setting[T](value: Setting[T], *, default: T) -> T:
        return default if value is UNSET else cast(T, value)

    @staticmethod
    def _mentions(policy: ResponseSpec) -> discord.AllowedMentions:
        mentions = policy.allowed_mentions
        return no_mentions() if mentions is UNSET or mentions is None else cast(discord.AllowedMentions, mentions)

    @staticmethod
    def _access(policy: ResponseSpec, actor_id: int | None) -> AccessPolicy:
        configured = policy.access
        if configured is UNSET or configured is None or configured is invoker_only:
            if actor_id is None:
                message = "live delivery without an identifiable actor requires explicit access"
                raise TypeError(message)
            return Owner(actor_id)
        if not callable(getattr(configured, "check", None)):
            message = "response access is not an AccessPolicy"
            raise TypeError(message)
        return cast(AccessPolicy, configured)

    async def close(self, *, reason: CloseReason = CloseReason.SCOPE_CLOSED) -> None:
        """Finish roots and sessions opened by this scope, then unregister it."""
        if self._closed:
            return
        await self._runtime._close_scope(self, reason=reason)
        self._closed = True


__all__ = ["Scope"]

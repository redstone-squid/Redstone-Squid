"""A lifetime for the roots one application object opens."""

from collections.abc import Hashable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Unpack, cast, overload

import discord

from squid_ui import paragraph
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui.text import Localization
from squid_ui_discord.access import AccessPolicy, Owner
from squid_ui_discord.audience import Audience
from squid_ui_discord.contracts import DocumentContent, FacadeContent, ResponseSource, SendDestination
from squid_ui_discord.delivery import Abandoned as DeliveryAbandoned
from squid_ui_discord.delivery import DeliveryResult, edit_to, no_mentions, send_to
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import PauseUpdates, RenewEphemeral
from squid_ui_discord.rendering import render_static
from squid_ui_discord.response import (
    UNSET,
    Abandoned,
    Presented,
    Rejected,
    Response,
    ResponseOverrides,
    ResponseResult,
    ResponseSpec,
    Sent,
    Setting,
    invoker_only,
)
from squid_ui_discord.session_specs import OpenContext, SessionOptions
from squid_ui_discord.sessions import Opened
from squid_ui_discord.sessions import Rejected as SessionRejected

if TYPE_CHECKING:
    from squid_ui_discord.request import Request
    from squid_ui_discord.runtime import DiscordUIRuntime


class Scope[OwnerT = Any]:
    """The roots one owner opened; ``close()`` finishes them and unregisters the scope.

    A request carries the scope of the cog (or app) it was dispatched to, so replies go
    through `Request`. `send` and `edit` cover the no-request case: a scheduled post, an
    edit of a message nobody just clicked.
    """

    def __init__(self, runtime: DiscordUIRuntime[discord.Client], owner: OwnerT, defaults: ResponseSpec) -> None:
        self._runtime = runtime
        self._owner = owner
        self._defaults = defaults
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
    def defaults(self) -> ResponseSpec:
        """The response policy applied after installation defaults."""
        return self._defaults

    @property
    def closed(self) -> bool:
        """Whether this scope has ended."""
        return self._closed

    def _require_live(self) -> None:
        if self._closed:
            message = "this Discord UI scope is closed"
            raise RuntimeError(message)

    def _policy(self, content: FacadeContent, overrides: ResponseOverrides) -> ResponseSpec:
        policy = self._runtime.response_defaults.overlay(self._defaults)
        screen_spec = getattr(type(content), "__response_spec__", None)
        if isinstance(screen_spec, ResponseSpec):
            policy = policy.overlay(screen_spec)
        return policy.overlay(None, **overrides)

    async def request(self, source: ResponseSource) -> Request[OwnerT]:
        """The request for `source`, created under this scope if no request has seen it yet."""
        self._require_live()
        from squid_ui_discord.request import request

        return await request(source, owner=self._owner)

    def render(self, content: FacadeContent, *, localization: Localization | None = None) -> MessagePayload:
        """Render supported static content through installed rendering defaults."""
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

    @overload
    async def respond[ComponentT: Component[ComponentsV2Target]](
        self,
        source: ResponseSource,
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
        source: ResponseSource,
        content: FacadeContent | Response,
        *,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult: ...

    async def respond(
        self,
        source: ResponseSource,
        content: FacadeContent | Response[Any],
        *,
        files: Sequence[discord.File] = (),
        parent: MessageRoot | None = None,
        session_key: Hashable | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Transitional: `(await scope.request(source)).respond(...)` for call sites not yet ported."""
        request = await self.request(source)
        return await request.respond(content, files=files, parent=parent, session_key=session_key, **overrides)

    async def send(
        self,
        destination: SendDestination,
        content: FacadeContent,
        *,
        locale: str | None = None,
        files: Sequence[discord.File] = (),
        **overrides: Unpack[ResponseOverrides],
    ) -> ResponseResult:
        """Send out-of-band content to a channel or DM destination."""
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
        )

    async def _present(
        self,
        content: FacadeContent,
        *,
        destination,
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
        parent_session = self._runtime.sessions.session_for(parent) if parent is not None else None
        existing_roots = frozenset(parent_session.message_roots) if parent_session is not None else frozenset()
        if parent is None:
            result = await configured.open(
                content,
                capture,
                sessions=self._runtime.sessions,
                open_context=open_context,
                key=session_key,
                **options,
            )
        else:
            result = await configured.attach(
                content,
                capture,
                sessions=self._runtime.sessions,
                open_context=open_context,
                parent=parent,
                **options,
            )
        if isinstance(result, SessionRejected):
            notice_delivery = None
            if result.notice is not None:
                notice_delivery = await destination(self.render(paragraph(result.notice), localization=localization))
            return Rejected(result.reason, notice_delivery)
        if isinstance(result, DeliveryAbandoned):
            return Abandoned()
        assert isinstance(result, Opened) and captured is not None
        root = result.session.root
        if parent is not None:
            attached = tuple(candidate for candidate in result.session.message_roots if candidate not in existing_roots)
            if len(attached) != 1:
                message = "session attachment did not produce exactly one child root"
                raise RuntimeError(message)
            root = attached[0]
        self._runtime._track(self, root)
        return Presented(content, root, result.session, captured)

    def _root_options(self, policy: ResponseSpec, localization: Localization) -> SessionOptions:
        options: SessionOptions = {}
        chrome = self._setting(policy.chrome, default=None)
        if chrome is not None:
            options["chrome"] = chrome
        options["timeout"] = self._setting(policy.timeout, default=180)
        options["localization"] = localization
        scheduler = self._runtime.scheduler if self._setting(policy.follow_topics, default=False) else None
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

    async def close(self) -> None:
        """Finish roots and sessions opened by this scope, then unregister it."""
        if self._closed:
            return
        await self._runtime._close_scope(self)
        self._closed = True


# Transitional name; removed once the bot is ported.
DiscordUI = Scope

__all__ = ["DiscordUI", "Scope"]

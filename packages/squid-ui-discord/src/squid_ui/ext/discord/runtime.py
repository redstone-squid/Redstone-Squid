"""Install and locate the Discord UI services owned by one client.

Installation assembles sessions, challenge handling, an execution host, and optional
scheduling without starting tasks. Enter runtime.running() to start those services and
close them before the client disconnects.

Commands and component callbacks recover those same services through DiscordUIRuntime.of,
using their interaction or other client-bearing source. A Scope groups the message roots
opened by one application owner, so unloading a cog can finish its live panels together.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Never, Unpack, cast, overload

import anyio
from anyio.abc import TaskStatus

import discord
from discord.ext import commands
from squid_ui.ext.discord.access import AccessPolicy
from squid_ui.ext.discord.challenges import ChallengeRunner, DialogPresenter
from squid_ui.ext.discord.config import Config
from squid_ui.ext.discord.contracts import LocalizationResolver
from squid_ui.ext.discord.delivery import DeliveryResult, Replyable
from squid_ui.ext.discord.facade import Scope
from squid_ui.ext.discord.lifetime import CloseReason, DiscordUIRuntimeMissing, Lifetime
from squid_ui.ext.discord.messages.contracts import MessageRootBehaviorOptions
from squid_ui.ext.discord.messages.defaults import MessageRootDefaults
from squid_ui.ext.discord.messages.root import MessageRoot
from squid_ui.ext.discord.messages.scheduler import MessageRootScheduler
from squid_ui.ext.discord.request import Request, request
from squid_ui.ext.discord.response import Presented, ResponseSpec, Sent
from squid_ui.ext.discord.sessions import SessionManager
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.interactions import ActionEvent
from squid_ui.runtime.definition import Component
from squid_ui.runtime.services import UIExecutionHost

type RuntimeSource = discord.Client | discord.Interaction[Any] | Replyable | discord.Message
"""Anything an installation can be found from: the client, or something carrying one."""


class DiscordUIRuntime[ClientT: discord.Client]:
    """The Discord runtime installed on one client; `close()` ends it.

    Use install to assemble shared services and defaults, then enter running() to supervise their
    tasks. Requests use of to find this same runtime
    from their source, and scope to associate live panels with an application owner.
    Closing the runtime finishes its scopes and sessions before unregistering it.

    Installation also resolves a construction dependency: the dialog presenter needs
    the session manager and challenge runner, while new sessions need that presenter.
    """

    def __init__(
        self,
        client: ClientT,
        *,
        sessions: SessionManager,
        challenges: ChallengeRunner,
        scheduler: MessageRootScheduler | None,
        localization: LocalizationResolver | None,
        response_defaults: ResponseSpec,
        config: Config,
    ) -> None:
        self._client = client
        self.sessions = sessions
        self.challenges = challenges
        self.scheduler = scheduler
        self.localization = localization
        self.response_defaults = response_defaults
        self.config = config
        self.execution_host = sessions.defaults.execution_host
        self._lifetime = Lifetime()
        self._lifetime.adopt(sessions._lifetime)

    @property
    def client(self) -> ClientT:
        """The client this runtime was installed on."""
        return self._client

    @property
    def defaults(self) -> MessageRootDefaults:
        """The session manager's current mount defaults, shared rather than copied."""
        return self.sessions.defaults

    @defaults.setter
    def defaults(self, defaults: MessageRootDefaults) -> None:
        self.sessions.defaults = defaults

    def mount(
        self,
        component: Component[ComponentsV2Target, Request[Any, ClientT]],
        *,
        access: AccessPolicy,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> MessageRoot:
        """Construct a mount from this host's defaults, applying per-call overrides.

        The reason a panel needs no object but the host: chrome, localization, the error
        hook and the challenge presenter all arrive with it.
        """
        return self.defaults.mount(component, access=access, **overrides)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Start the runtime and close it on exit. Enter inside the client context."""
        async with anyio.create_task_group() as tasks:
            try:
                await tasks.start(self.run)
                yield
            finally:
                with anyio.CancelScope(shield=True):
                    await self.close()
                tasks.cancel_scope.cancel()

    async def run(self, *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED) -> None:
        """Run the scheduler, challenges, and execution host until the caller cancels.

        Use ``await tasks.start(runtime.run)`` to wait until services can be mounted.
        Applications that track each job's health can start ``scheduler.run()``,
        ``challenges.run()``, and ``execution_host.run()`` separately instead.
        """
        async with anyio.create_task_group() as tasks:
            if self.scheduler is not None:
                tasks.start_soon(self.scheduler.run)
            tasks.start_soon(self.challenges.run)
            if self.execution_host is not None:
                await tasks.start(self.execution_host.run)
            task_status.started()

    async def close(self) -> None:
        """Finish the sessions, close the execution host, and unregister this runtime.

        The application's task group still owns ``run()``. Cancel it there when the
        application is ready to stop the scheduler and challenge runner too.
        """
        errors: list[Exception] = []
        try:
            self._lifetime.closing = True
            try:
                await self._lifetime.close_children(reason=CloseReason.SHUTDOWN)
            except Exception as error:
                errors.append(error)
        finally:
            try:
                if self.execution_host is not None:
                    await self.execution_host.close()
            finally:
                if Lifetime.installations.get(self.client) is self:
                    del Lifetime.installations[self.client]
        if errors:
            message = "Discord runtime shutdown failed"
            raise ExceptionGroup(message, errors)

    @classmethod
    @overload
    def of[SourceClientT: discord.Client](
        cls, source: SourceClientT | discord.Interaction[SourceClientT]
    ) -> DiscordUIRuntime[SourceClientT]: ...

    @classmethod
    @overload
    def of[BotT: commands.Bot](cls, source: commands.Context[BotT]) -> DiscordUIRuntime[BotT]: ...

    @classmethod
    @overload
    def of(cls, source: Replyable | discord.Message) -> DiscordUIRuntime[Any]: ...

    @classmethod
    def of(cls, source: RuntimeSource) -> DiscordUIRuntime[Any]:
        """The host installed on the client `source` names.

        Raises:
            DiscordUIRuntimeMissing: Nothing was installed on it.
        """
        return Lifetime.runtime_for(source)

    @overload
    def scope(self, owner: None) -> Never: ...

    @overload
    def scope[OwnerT](self, owner: OwnerT) -> Scope[OwnerT, ClientT]: ...

    def scope[OwnerT](self, owner: OwnerT | None) -> Scope[OwnerT, ClientT]:
        """Return the one live scope registered for ``owner`` by exact identity."""
        if owner is None:
            message = "None cannot own a Discord UI scope"
            raise TypeError(message)
        for node in self._lifetime.children:
            scope = node.scope
            if scope is None:
                continue
            if scope.owner is owner and not scope.closed:
                return cast(Scope[OwnerT, ClientT], scope)
        scope = Scope(self, owner)
        self._lifetime.adopt(scope._lifetime)
        return scope

    @property
    def app(self) -> Scope[ClientT, ClientT]:
        """The scope owned by the client itself, where unowned requests land."""
        return self.scope(self.client)

    def scope_for[OwnerT](self, owner: OwnerT) -> Scope[OwnerT, ClientT]:
        """Return an owner's registered live scope, raising if it is missing or closed."""
        for node in self._lifetime.children:
            scope = node.scope
            if scope is None:
                continue
            if scope.owner is owner and not scope.closed:
                return cast(Scope[OwnerT, ClientT], scope)
        message = f"{type(owner).__name__} has no live UI scope; register it before dispatch"
        raise RuntimeError(message)

    def scope_of_root(self, root: MessageRoot | None) -> Scope[Any, ClientT]:
        """Find invocation authority through the presentation's lifetime owner."""
        if root is not None:
            if root.finished:
                message = "the component request belongs to a finished message root"
                raise RuntimeError(message)
            scope = root._lifetime.scope_owner()
            if scope is not None:
                if scope.runtime is not self:
                    message = "the component belongs to a different Discord runtime"
                    raise ValueError(message)
                return cast(Scope[Any, ClientT], scope)
        return self.app

    def _track[OwnerT](self, scope: Scope[OwnerT, Any], message_root: MessageRoot) -> None:
        existing = message_root._lifetime.scope_owner()
        if existing is not None:
            if existing is not scope:
                message = "presentation belongs to another scope"
                raise ValueError(message)
            return
        scope._lifetime.adopt(message_root._lifetime)

    async def _handle_component_error(self, event: ActionEvent, error: Exception, source: str) -> None:
        """Present and observe a rolled-back component failure through the installed policy."""
        policy = self.config.errors
        if policy.render is None and policy.observe is None:
            logging.getLogger(__name__).error("unhandled component error in %s", source, exc_info=error)
            return
        req = await request(event)
        delivery: DeliveryResult | None = None
        try:
            if policy.render is not None:
                content = policy.render(req, error)
                audience = req.audience if req.deferred is not None else "personal"
                result = await req.respond(content, audience=audience or "personal")
                if isinstance(result, Sent | Presented):
                    delivery = result.delivery
        finally:
            if policy.observe is not None:
                await policy.observe(req, error, delivery)

    async def _close_scope[OwnerT](self, scope: Scope[OwnerT, Any], *, reason: CloseReason) -> None:
        try:
            await scope._lifetime.close_children(reason=reason)
        finally:
            scope._lifetime.detach()


def install[ClientT: discord.Client](
    client: ClientT,
    config: Config | None = None,
) -> DiscordUIRuntime[ClientT]:
    """Assemble and register the UI runtime for a client without starting its tasks.

    The scheduler delivers live reactive updates with or without a topic bus.
    External dependencies carry their owning bus in topic handles. The localization
    callback resolves the locale for each new Request.

    Raises:
        ValueError: A runtime is already installed on the client.
    """
    selected = config or Config()
    defaults = selected.defaults
    if defaults.execution_host is None:
        defaults = defaults.replace(execution_host=UIExecutionHost())
    bus = selected.bus
    profiler = selected.profiler
    localization = selected.localization
    if Lifetime.installations.get(client) is not None:
        message = "client already has a layout host installed"
        raise ValueError(message)
    scheduler = MessageRootScheduler(bus, profiler=profiler)
    defaults = defaults.replace(scheduler=scheduler)
    sessions = SessionManager(defaults=defaults)
    challenges = ChallengeRunner()
    # The knot install exists to tie: the presenter needs the registry and the runner, and
    # the registry needs the presenter to hand every mount it opens.
    sessions.defaults = sessions.defaults.replace(challenge=DialogPresenter(sessions, challenges))
    runtime = DiscordUIRuntime(
        client,
        sessions=sessions,
        challenges=challenges,
        scheduler=scheduler,
        localization=localization,
        response_defaults=selected.responses,
        config=selected,
    )
    Lifetime.installations[client] = runtime
    return runtime


__all__ = [
    "DiscordUIRuntime",
    "DiscordUIRuntimeMissing",
    "LocalizationResolver",
    "RuntimeSource",
    "install",
]

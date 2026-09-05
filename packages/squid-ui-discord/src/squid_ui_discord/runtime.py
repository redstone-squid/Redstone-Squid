"""One installed Discord runtime, reachable from the client it was installed on.

:class:`MessageRootDefaults` answered *construction*: the values every mount on this host shares.
It cannot answer *lookup*, because it is a value — nothing hands one back from an
interaction. A challenge presenter needs the session registry and the background runner, and
a panel opened from a click holds neither, so a host that wanted both ended up minting a
process global to stand in for the lookup this module now offers.

:func:`install` performs the assembly once — registry, challenge runner, dialog presenter,
and optionally a scheduler — and records the result against the client. Anything carrying that
client reaches it again through :meth:`DiscordUIRuntime.of`. The client-keyed weak table is the
same shape :mod:`squid_ui_discord.routing` already uses for installed routers.

Nothing here starts a task. `DiscordUIRuntime.run()` is offered for a host that wants one job, and
declined by a host that wants per-job health granularity; either way the host supervises it.
"""

import weakref
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Never, Unpack, cast, overload

import anyio
import discord

from squid_ui.errors import SquidUiError
from squid_ui.profiling import Profiler
from squid_ui.runtime.component import Component
from squid_ui.runtime.topics import TopicBus
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.challenges import ChallengeRunner, DialogPresenter
from squid_ui_discord.config import DiscordUIConfig
from squid_ui_discord.contracts import LocalizationResolver
from squid_ui_discord.delivery import Replyable
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import MessageRootBehaviorOptions
from squid_ui_discord.message_root_options import MessageRootDefaults
from squid_ui_discord.message_root_scheduler import MessageRootScheduler
from squid_ui_discord.sessions import SessionManager

if TYPE_CHECKING:
    from squid_ui_discord.facade import Scope
    from squid_ui_discord.response import ResponseSpec

type RuntimeSource = discord.Client | discord.Interaction[Any] | Replyable | discord.Message
"""Anything an installation can be found from: the client, or something carrying one."""

_INSTALLED: weakref.WeakKeyDictionary[Any, DiscordUIRuntime[Any]] = weakref.WeakKeyDictionary()
"""Hosts installed per client, so a second `install` on one client can be refused."""


class DiscordUIRuntimeMissing(SquidUiError, LookupError):
    """Nothing was installed on the client this source names.

    A wiring bug rather than a runtime condition: every reachable path from a click runs
    through a client the host built, so the answer is to call :func:`install` at
    construction, not to guard each call site.
    """


def _candidates(source: RuntimeSource) -> Iterator[Any]:
    """The client `source` might be, then the ones it might carry.

    Duck-typed rather than isinstance-dispatched so a test double reaches its own
    installation: `discord.Interaction` carries `client`, a command context carries `bot`,
    and a client is itself.
    """
    yield source
    for attribute in ("client", "bot"):
        carried = getattr(source, attribute, None)
        if carried is not None:
            yield carried
    state = getattr(source, "_state", None)
    get_client = getattr(state, "_get_client", None)
    if callable(get_client):
        yield get_client()


class DiscordUIRuntime[ClientT: discord.Client]:
    """The Discord runtime installed on one client; `close()` ends it.

    Holds the objects whose construction is circular — the session registry, the challenge
    runner, and the dialog presenter that needs both — plus the scheduler, when the installing
    host gave a topic bus to build one from. Built by :func:`install`, never directly, so
    exactly one host exists per client and :meth:`of` can be trusted.
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
        config: DiscordUIConfig,
    ) -> None:
        self.client = client
        self.sessions = sessions
        self.challenges = challenges
        self.scheduler = scheduler
        self.localization = localization
        self.response_defaults = response_defaults
        self.config = config
        self._scopes: list[Scope[Any]] = []
        self._scope_roots: dict[int, set[MessageRoot]] = {}

    @property
    def defaults(self) -> MessageRootDefaults:
        """The values every mount on this host is built from.

        The manager's own defaults, not a copy: a message root opened through `sessions.open` and one
        built here are wired the same way, which is the whole reason this lookup exists.
        """
        return self.sessions.defaults

    @defaults.setter
    def defaults(self, defaults: MessageRootDefaults) -> None:
        self.sessions.defaults = defaults

    def mount(
        self,
        component: Component[ComponentsV2Target],
        *,
        access: AccessPolicy,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> MessageRoot:
        """Construct a mount from this host's defaults, applying per-call overrides.

        The reason a panel needs no object but the host: chrome, localization, the error
        hook and the challenge presenter all arrive with it.
        """
        return self.defaults.mount(component, access=access, **overrides)

    async def run(self) -> None:
        """Serve this host's scheduler and challenge runner until the caller cancels.

        A convenience for a host content to supervise one job. A host wanting per-job health
        granularity starts `scheduler.run()` and `challenges.run()` separately instead; the
        package still starts nothing on its own either way.
        """
        # A task group rather than gather: a failing job must cancel the other one rather
        # than leave a half-served host running.
        async with anyio.create_task_group() as tasks:
            if self.scheduler is not None:
                tasks.start_soon(self.scheduler.run)
            tasks.start_soon(self.challenges.run)

    async def close(self) -> None:
        """Finish every session, then stop answering :meth:`of` for this client.

        Does not cancel `run()`: the task belongs to whoever started it, and a supervisor
        that owns the job is the thing entitled to end it.
        """
        try:
            for scope in tuple(self._scopes):
                await scope.close()
            await self.sessions.close_all()
        finally:
            if _INSTALLED.get(self.client) is self:
                del _INSTALLED[self.client]

    @classmethod
    def of(cls, source: RuntimeSource) -> DiscordUIRuntime[Any]:
        """The host installed on the client `source` names.

        Raises:
            DiscordUIRuntimeMissing: Nothing was installed on it.
        """
        for candidate in _candidates(source):
            try:
                runtime = _INSTALLED.get(candidate)
            except TypeError:
                # Unhashable, or not weak-referenceable: it was never a key here.
                continue
            if runtime is not None:
                return runtime
        message = (
            f"no layout host is installed on this {type(source).__name__}; call sd.install(client) once at startup"
        )
        raise DiscordUIRuntimeMissing(message)

    @overload
    def scope(self, owner: None, *, defaults: ResponseSpec | None = None) -> Never: ...

    @overload
    def scope[OwnerT](self, owner: OwnerT, *, defaults: ResponseSpec | None = None) -> Scope[OwnerT]: ...

    def scope[OwnerT](self, owner: OwnerT | None, *, defaults: ResponseSpec | None = None) -> Scope[OwnerT]:
        """Return the one live scope registered for ``owner`` by exact identity."""
        from squid_ui_discord.facade import Scope
        from squid_ui_discord.response import ResponseSpec

        if owner is None:
            message = "None cannot own a Discord UI scope"
            raise TypeError(message)
        selected = ResponseSpec() if defaults is None else defaults
        for scope in self._scopes:
            if scope.owner is owner and not scope.closed:
                if defaults is not None and scope.defaults != selected:
                    message = "owner is already registered with different response defaults"
                    raise ValueError(message)
                return cast(Scope[OwnerT], scope)
        scope = Scope(cast(DiscordUIRuntime[discord.Client], self), owner, selected)
        self._scopes.append(cast(Scope[Any], scope))
        self._scope_roots[id(scope)] = set()
        return scope

    @property
    def app(self) -> Scope[ClientT]:
        """The scope owned by the client itself, where unowned requests land."""
        return self.scope(self.client)

    def scope_for[OwnerT](self, owner: OwnerT) -> Scope[OwnerT]:
        """The live scope registered for `owner`, or the app scope if it never registered one.

        A cog that skipped `ui_load` still gets its commands answered; it just does not get
        its own lifetime for what they open.
        """
        for scope in self._scopes:
            if scope.owner is owner and not scope.closed:
                return cast("Scope[OwnerT]", scope)
        return cast("Scope[OwnerT]", self.app)

    def scope_of_root(self, root: MessageRoot | None) -> Scope[Any]:
        """The scope that tracked `root`, so a click inherits the lifetime of the panel it hit."""
        if root is not None:
            for scope in self._scopes:
                if root in self._scope_roots.get(id(scope), ()):
                    return scope
        return self.app

    def _track[OwnerT](self, scope: Scope[OwnerT], message_root: MessageRoot) -> None:
        self._scope_roots[id(scope)].add(message_root)

    async def _close_scope[OwnerT](self, scope: Scope[OwnerT]) -> None:
        roots = self._scope_roots.pop(id(scope), set())
        for message_root in tuple(roots):
            session = self.sessions.session_for(message_root)
            if session is not None:
                await session.finish()
            else:
                await message_root.finish()
        self._scopes = [candidate for candidate in self._scopes if candidate is not scope]


def install[ClientT: discord.Client](
    client: ClientT,
    config: DiscordUIConfig | None = None,
    *,
    defaults: MessageRootDefaults | None = None,
    bus: TopicBus | None = None,
    profiler: Profiler | None = None,
    localization: LocalizationResolver | None = None,
) -> DiscordUIRuntime[ClientT]:
    """Assemble the Discord runtime for `client` and record it against the client.

    `bus` is what makes a scheduler: with one, message roots refresh from topics and shared state, and
    the scheduler becomes the default scheduler; without one, a mount is refreshed only by its
    own clicks. `profiler` instruments that scheduler. `localization` is the host's one async
    hook for resolving a request's render-time locale; it runs when an owner scope resolves
    the request.

    Raises:
        ValueError: A host is already installed on this client. One client has one host, the
            way one client has one router per id language -- a second would give the same
            click two answers.
    """
    from squid_ui_discord.response import DEFAULT_RESPONSE_SPEC

    if config is not None and any(value is not None for value in (defaults, bus, profiler, localization)):
        message = "pass DiscordUIConfig or legacy installation keywords, not both"
        raise TypeError(message)
    selected = config or DiscordUIConfig(
        defaults=MessageRootDefaults() if defaults is None else defaults,
        bus=bus,
        profiler=profiler,
        localization=localization,
    )
    defaults = selected.defaults
    bus = selected.bus
    profiler = selected.profiler
    localization = selected.localization
    if _INSTALLED.get(client) is not None:
        message = "client already has a layout host installed"
        raise ValueError(message)
    scheduler = None if bus is None else MessageRootScheduler(bus, profiler=profiler)
    if scheduler is not None:
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
        response_defaults=DEFAULT_RESPONSE_SPEC.overlay(selected.responses),
        config=selected,
    )
    _INSTALLED[client] = runtime
    return runtime


__all__ = [
    "DiscordUIRuntime",
    "DiscordUIRuntimeMissing",
    "LocalizationResolver",
    "RuntimeSource",
    "install",
]

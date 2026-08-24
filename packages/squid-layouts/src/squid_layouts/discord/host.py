"""One installed Discord runtime, reachable from the client it was installed on.

:class:`MountDefaults` answered *construction*: the values every mount on this host shares.
It cannot answer *lookup*, because it is a value — nothing hands one back from an
interaction. A challenge presenter needs the session registry and the background runner, and
a panel opened from a click holds neither, so a host that wanted both ended up minting a
process global to stand in for the lookup this module now offers.

:func:`install` performs the assembly once — registry, challenge runner, dialog presenter,
and optionally a reactor — and records the result against the client. Anything carrying that
client reaches it again through :meth:`LayoutHost.of`. The client-keyed weak table is the
same shape :mod:`squid_layouts.discord.routing` already uses for installed routers.

Nothing here starts a task. `LayoutHost.run()` is offered for a host that wants one job, and
declined by a host that wants per-job health granularity; either way the host supervises it.
"""

import weakref
from collections.abc import Iterator
from typing import Any, Unpack

import anyio
import discord

from squid_layouts.discord.access import AccessPolicy
from squid_layouts.discord.challenges import ChallengeRunner, DialogPresenter
from squid_layouts.discord.defaults import MountDefaults, MountOptions
from squid_layouts.discord.delivery import Replyable
from squid_layouts.discord.mount import Mount
from squid_layouts.discord.reactor import Reactor
from squid_layouts.discord.sessions import SessionRegistry
from squid_layouts.profiling import Profiler
from squid_layouts.runtime.component import Component
from squid_layouts.runtime.topics import TopicBus

type HostSource = discord.Client | discord.Interaction[Any] | Replyable
"""Anything an installation can be found from: the client, or something carrying one."""

_INSTALLED: weakref.WeakKeyDictionary[Any, LayoutHost[Any]] = weakref.WeakKeyDictionary()
"""Hosts installed per client, so a second `install` on one client can be refused."""


class LayoutHostMissing(LookupError):
    """Nothing was installed on the client this source names.

    A wiring bug rather than a runtime condition: every reachable path from a click runs
    through a client the host built, so the answer is to call :func:`install` at
    construction, not to guard each call site.
    """


def _candidates(source: HostSource) -> Iterator[Any]:
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


class LayoutHost[ClientT: discord.Client]:
    """The Discord runtime installed on one client; `close()` ends it.

    Holds the objects whose construction is circular — the session registry, the challenge
    runner, and the dialog presenter that needs both — plus the reactor, when the installing
    host gave a topic bus to build one from. Built by :func:`install`, never directly, so
    exactly one host exists per client and :meth:`of` can be trusted.
    """

    def __init__(
        self,
        client: ClientT,
        *,
        mounts: SessionRegistry,
        challenges: ChallengeRunner,
        reactor: Reactor | None,
    ) -> None:
        self.client = client
        self.mounts = mounts
        self.challenges = challenges
        self.reactor = reactor

    @property
    def defaults(self) -> MountDefaults:
        """The values every mount on this host is built from.

        The registry's own defaults, not a copy: a mount opened through `mounts.open` and one
        built here are wired the same way, which is the whole reason this lookup exists.
        """
        return self.mounts.defaults

    @defaults.setter
    def defaults(self, defaults: MountDefaults) -> None:
        self.mounts.defaults = defaults

    def mount(self, component: Component, *, access: AccessPolicy, **overrides: Unpack[MountOptions]) -> Mount:
        """Construct a mount from this host's defaults, applying per-call overrides.

        The reason a panel needs no object but the host: chrome, localization, the error
        hook and the challenge presenter all arrive with it.
        """
        return self.defaults.mount(component, access=access, **overrides)

    async def run(self) -> None:
        """Serve this host's reactor and challenge runner until the caller cancels.

        A convenience for a host content to supervise one job. A host wanting per-job health
        granularity starts `reactor.run()` and `challenges.run()` separately instead; the
        package still starts nothing on its own either way.
        """
        # A task group rather than gather: a failing job must cancel the other one rather
        # than leave a half-served host running.
        async with anyio.create_task_group() as tasks:
            if self.reactor is not None:
                tasks.start_soon(self.reactor.run)
            tasks.start_soon(self.challenges.run)

    async def close(self) -> None:
        """Finish every session, then stop answering :meth:`of` for this client.

        Does not cancel `run()`: the task belongs to whoever started it, and a supervisor
        that owns the job is the thing entitled to end it.
        """
        try:
            await self.mounts.close_all()
        finally:
            if _INSTALLED.get(self.client) is self:
                del _INSTALLED[self.client]

    @classmethod
    def of(cls, source: HostSource) -> LayoutHost[Any]:
        """The host installed on the client `source` names.

        Raises:
            LayoutHostMissing: Nothing was installed on it.
        """
        for candidate in _candidates(source):
            try:
                host = _INSTALLED.get(candidate)
            except TypeError:
                # Unhashable, or not weak-referenceable: it was never a key here.
                continue
            if host is not None:
                return host
        message = (
            f"no layout host is installed on this {type(source).__name__}; "
            "call sl.discord.install(client) once at startup"
        )
        raise LayoutHostMissing(message)


def install[ClientT: discord.Client](
    client: ClientT,
    *,
    defaults: MountDefaults = MountDefaults(),  # noqa: B008  # frozen value
    bus: TopicBus | None = None,
    profiler: Profiler | None = None,
) -> LayoutHost[ClientT]:
    """Assemble the Discord runtime for `client` and record it against the client.

    `bus` is what makes a reactor: with one, mounts refresh from topics and shared state, and
    the reactor becomes the default scheduler; without one, a mount is refreshed only by its
    own clicks. `profiler` instruments that reactor.

    Raises:
        ValueError: A host is already installed on this client. One client has one host, the
            way one client has one router per id language -- a second would give the same
            click two answers.
    """
    if _INSTALLED.get(client) is not None:
        message = "client already has a layout host installed"
        raise ValueError(message)
    reactor = None if bus is None else Reactor(bus, profiler=profiler)
    if reactor is not None:
        defaults = defaults.replace(scheduler=reactor)
    mounts = SessionRegistry(defaults=defaults)
    challenges = ChallengeRunner()
    # The knot install exists to tie: the presenter needs the registry and the runner, and
    # the registry needs the presenter to hand every mount it opens.
    mounts.defaults = mounts.defaults.replace(challenge=DialogPresenter(mounts, challenges))
    host = LayoutHost(client, mounts=mounts, challenges=challenges, reactor=reactor)
    _INSTALLED[client] = host
    return host


__all__ = ["HostSource", "LayoutHost", "LayoutHostMissing", "install"]

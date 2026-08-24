"""Reusable host defaults for Discord mounts."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TypedDict, Unpack

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.discord.access import AccessPolicy
from squid_layouts.discord.mount import (
    DEFAULT_EXPIRY,
    ChallengePresenter,
    ErrorHook,
    ExpiryPolicy,
    Mount,
    Scheduler,
    _monotonic,
)
from squid_layouts.discord.target import V2_TARGET, Target
from squid_layouts.interactions import ActionMiddleware
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.navigation import NavFactory
from squid_layouts.profiling import Profiler
from squid_layouts.runtime.component import Component
from squid_layouts.text import NEUTRAL, Localization


class MountOptions(TypedDict, total=False):
    """Per-mount overrides accepted by :meth:`MountDefaults.mount`."""

    target: Target
    chrome: Chrome
    localization: Localization
    palette: Palette
    strict: bool
    timeout: float | None
    on_error: ErrorHook | None
    middleware: Sequence[ActionMiddleware]
    profiler: Profiler | None
    scheduler: Scheduler | None
    expiry: ExpiryPolicy | None
    nav: NavFactory | None
    challenge: ChallengePresenter | None
    acknowledgement_timeout: float
    pending_after: float
    clock: Callable[[], float]


@dataclass(frozen=True, slots=True)
class MountDefaults:
    """Host-wide values used to construct mounts.

    Access remains deliberately absent: it identifies the actor allowed to use a specific
    mount and must be supplied at each construction site.
    """

    target: Target = V2_TARGET
    chrome: Chrome = DEFAULT_CHROME
    localization: Localization = NEUTRAL
    palette: Palette = DEFAULT_PALETTE
    strict: bool = False
    timeout: float | None = 900
    on_error: ErrorHook | None = None
    middleware: Sequence[ActionMiddleware] = ()
    profiler: Profiler | None = None
    scheduler: Scheduler | None = None
    expiry: ExpiryPolicy | None = DEFAULT_EXPIRY
    nav: NavFactory | None = None
    challenge: ChallengePresenter | None = None
    acknowledgement_timeout: float = 2.5
    pending_after: float = 1.0
    clock: Callable[[], float] = _monotonic

    def mount(
        self,
        component: Component,
        *,
        access: AccessPolicy,
        **overrides: Unpack[MountOptions],
    ) -> Mount:
        """Construct a mount, applying per-call overrides over these defaults."""
        configured = self.replace(**overrides)
        return Mount(
            component,
            access=access,
            target=configured.target,
            chrome=configured.chrome,
            localization=configured.localization,
            palette=configured.palette,
            strict=configured.strict,
            timeout=configured.timeout,
            on_error=configured.on_error,
            middleware=configured.middleware,
            profiler=configured.profiler,
            scheduler=configured.scheduler,
            expiry=configured.expiry,
            nav=configured.nav,
            challenge=configured.challenge,
            acknowledgement_timeout=configured.acknowledgement_timeout,
            pending_after=configured.pending_after,
            clock=configured.clock,
        )

    def replace(self, **changes: Unpack[MountOptions]) -> MountDefaults:
        """Return a copy with selected host defaults replaced."""
        return replace(self, **changes)

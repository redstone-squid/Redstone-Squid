"""Host-facing contracts of a message root: hooks, schedulers, challenges, and expiry.

Everything here is a protocol, policy value, or read-only snapshot that a host implements
or consumes; the behaviour they describe lives in :mod:`squid_ui_discord.message_root`.
"""

import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, Self, TypedDict, Unpack, cast, runtime_checkable

import discord

from squid_reactivity.topics import Address, TopicBus
from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.guards import Challenge
from squid_ui.interactions import ActionMiddleware
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.navigation import NavFactory
from squid_ui.planning.result import PlanMetrics, PlanReport
from squid_ui.planning.target import Target
from squid_ui.profiling import Profiler
from squid_ui.runtime.services import UIExecutionHost
from squid_ui.target_types import (
    ComponentsV2Target,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    DiscordTarget,
)
from squid_ui.text import NEUTRAL, Localization, TextLike
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.render_cache import RenderProgramCache, RenderProgramCacheSnapshot
from squid_ui_discord.target import DISCORD_V2_DPY27

if TYPE_CHECKING:
    from squid_ui_discord.message_root import AnyMessageRoot


class ErrorHook(Protocol):
    """Host-provided handler for exceptions escaping a component callback."""

    def __call__(self, interaction: discord.Interaction, error: Exception, source: str) -> Awaitable[None]: ...


class FinishHook(Protocol):
    """Observer told that a mount has finished, after its teardown."""

    # Positional-only, as `MessageDestination` is: a named parameter would make the protocol demand
    # that every observer spell the argument `mount`.
    def __call__(self, message_root: AnyMessageRoot, /) -> Awaitable[None]: ...


class PresentedHook(Protocol):
    """Observer told that Discord accepted and the mount committed a generation.

    Synchronous on purpose: it runs at the commit point, under the lock every operation
    that can replace the visible message shares, so a hook that could await would be able
    to wait on the mount that is calling it.
    """

    def __call__(self, message_root: AnyMessageRoot, /) -> None: ...


class CommittedHook(Protocol):
    """Observer told that an application render committed its runtime state.

    Synchronous for the same reason as `PresentedHook`: commits run under the shared
    render lock, where awaiting or re-entering the mount would deadlock.
    """

    def __call__(self, message_root: AnyMessageRoot, /) -> None: ...


class Scheduler(Protocol):
    """Anything that can absorb out-of-band refresh requests (see `MessageRootScheduler`)."""

    def schedule(self, message_root: AnyMessageRoot) -> None: ...


@runtime_checkable
class ProfiledScheduler(Protocol):
    """A scheduler that carries the profiler its mounts should inherit."""

    @property
    def profiler(self) -> Profiler: ...


@runtime_checkable
class ReactiveScheduler(Protocol):
    """A scheduler that can preserve the component attribution of a bus change."""

    def schedule_reactive(self, message_root: AnyMessageRoot, address: Address) -> None: ...


type ResumedPress = Callable[[], Awaitable[None]]


class ChallengeSupervisor(Protocol):
    """Queue approved presses outside the approving handler's transaction context.

    Start the consumer before interactions arrive. Spawning directly from the approving
    handler inherits its ContextVars, so a separate task alone does not isolate the resumed
    action. ``resume`` enqueues work synchronously rather than awaiting it.
    """

    def resume(self, press: ResumedPress) -> None: ...


@dataclass(frozen=True, slots=True)
class ChallengeRequest:
    """One press that stopped to ask its actor a question, and the two answers it takes.

    `approve` *is* the resumed press: it re-enters `MessageRoot.dispatch` from the top and runs
    the whole action. It must therefore be handed to a `ChallengeSupervisor` rather than
    awaited from the dialog's own handler. `decline` only records the refusal and delivers
    the challenge's wording, so it is safe to await anywhere.
    """

    message_root: AnyMessageRoot
    interaction: discord.Interaction
    """The original press, whose response the presenter uses for the question.

    Approval retains this actor identity but does not reuse its handle for the parent message.
    """
    challenge: Challenge
    key: str
    """The routed binding key the approval resumes, which is what carries a grouped select's route."""
    approve: ResumedPress
    decline: ResumedPress


class ChallengePresenter(Protocol):
    """Shows a challenge and arranges for the answer to run outside the answering press.

    Host-supplied, because a mount cannot open a dialog by itself: it holds no session
    registry -- the lookup runs the other way -- and no supervisor. A mount whose guard
    challenges without one configured treats that as a programmer error.
    """

    async def present(self, request: ChallengeRequest) -> None: ...


@runtime_checkable
class ExpirySupervisor(Protocol):
    """A scheduler that observes mount edit-authority deadlines."""

    def watch(self, message_root: AnyMessageRoot) -> Callable[[], None]: ...


@runtime_checkable
class TopicScheduler(Protocol):
    """A scheduler exposing the bus used for shared-state and topic subscriptions.

    Schedulers without a bus can still refresh roots explicitly, but cannot subscribe
    them to changes published elsewhere.
    """

    bus: TopicBus

    def schedule(self, message_root: AnyMessageRoot) -> None: ...


def _validate_warning(warning: float) -> None:
    if not math.isfinite(warning) or warning <= 0:
        message = "an expiry warning must be a finite positive number of seconds"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PauseUpdates:
    """Show status chrome before temporary edit authority expires."""

    warning: float = 60.0
    """Seconds before the edit token expires to request the status update."""

    def __post_init__(self) -> None:
        _validate_warning(self.warning)


@dataclass(frozen=True, slots=True)
class RenewEphemeral:
    """Replace an expiring ephemeral panel with an explicit renewal screen."""

    warning: float = 90.0
    """Seconds before the edit token expires to request the renewal screen."""
    label: TextLike | None = None

    def __post_init__(self) -> None:
        _validate_warning(self.warning)


type ExpiryPolicy = PauseUpdates | RenewEphemeral


DEFAULT_EXPIRY = PauseUpdates()


def monotonic() -> float:
    """The default clock a message root ages against."""
    return time.monotonic()


class MessageRootBehaviorOptions(TypedDict, total=False):
    """Target-independent keywords that configure a message root."""

    chrome: Chrome
    localization: Localization
    palette: Palette
    strict: bool
    timeout: float | None
    retain_routed_on_timeout: bool
    on_error: ErrorHook | None
    middleware: Sequence[ActionMiddleware]
    profiler: Profiler | None
    render_cache: RenderProgramCache | None
    scheduler: Scheduler | None
    execution_host: UIExecutionHost | None
    expiry: ExpiryPolicy | None
    nav: NavFactory | None
    challenge: ChallengePresenter | None
    acknowledgement_timeout: float
    pending_after: float
    clock: Callable[[], float]


class MessageRootOptions[
    RenderTargetT: DiscordTarget = ComponentsV2Target,
    AdapterT: DiscordPyAdapter = DiscordPy27Adapter,
](MessageRootBehaviorOptions, total=False):
    """Every keyword that configures a message root, as a forwardable bundle.

    Paired with :class:`MessageRootConfig`, which holds the same set with its defaults. Two
    declarations is the floor: a TypedDict cannot be derived from a dataclass at type-check
    time. `tests/test_sessions.py` pins them against each other, and `access` is in neither
    -- it identifies who may use one specific mount, so it is never a default.
    """

    target: Target[Any, Any, RenderTargetT, AdapterT]


@dataclass(frozen=True, slots=True)
class MessageRootConfig[
    RenderTargetT: DiscordTarget = ComponentsV2Target,
    AdapterT: DiscordPyAdapter = DiscordPy27Adapter,
]:
    """Everything a message root is configured with, and what each value defaults to.

    The single home for those defaults: `MessageRoot.__init__` reads them from here rather
    than restating them, and a host that wants different ones builds one of these instead of
    repeating keywords at every construction site.
    """

    target: Target[Any, Any, RenderTargetT, AdapterT] = cast(
        Target[Any, Any, RenderTargetT, AdapterT], DISCORD_V2_DPY27
    )
    chrome: Chrome = DEFAULT_CHROME
    localization: Localization = NEUTRAL
    palette: Palette = DEFAULT_PALETTE
    strict: bool = False
    timeout: float | None = 900
    """Idle seconds since initial delivery or accepted interaction; None disables the timer."""
    retain_routed_on_timeout: bool = False
    on_error: ErrorHook | None = None
    middleware: Sequence[ActionMiddleware] = ()
    profiler: Profiler | None = None
    render_cache: RenderProgramCache | None = None
    scheduler: Scheduler | None = None
    execution_host: UIExecutionHost | None = None
    """Running owner of service activity and asynchronous panel shutdown."""
    expiry: ExpiryPolicy | None = DEFAULT_EXPIRY
    nav: NavFactory | None = None
    challenge: ChallengePresenter | None = None
    acknowledgement_timeout: float = 2.5
    """Seconds into handler execution before deferring an unanswered interaction."""
    pending_after: float = 1.0
    """Seconds before a BusySpec action requests its interim presentation."""
    clock: Callable[[], float] = monotonic

    def replace(self, **changes: Unpack[MessageRootOptions[RenderTargetT, AdapterT]]) -> Self:
        """Return a copy with selected values replaced."""
        return replace(self, **changes)


DEFAULT_MESSAGE_ROOT_CONFIG = MessageRootConfig()


class MessageRootStatus(StrEnum):
    """Which mount-owned generation the reader can currently see."""

    ACTIVE = "active"
    RENEWAL_ARMED = "renewal_armed"


@dataclass(frozen=True, slots=True)
class MessageAddress:
    """Where a mount's message is -- for links and diagnostics, never for writing to it.

    Writing goes through an :class:`~squid_ui_discord.delivery.EditHandle`, which is
    about credentials and when they expire. These are only coordinates, so they stay true
    after every handle to the message has gone stale.
    """

    message_id: int
    channel_id: int
    guild_id: int | None
    jump_url: str
    ephemeral: bool


@dataclass(frozen=True, slots=True)
class MessageRootSnapshot:
    """A shallow diagnostic snapshot of a message root.

    Timing values use seconds. Component state and presentation state are inspected
    separately through runtime and presentation. Objects such as a custom access policy
    are referenced rather than copied; freezing this record does not freeze those objects.
    """

    id: str
    component: str
    """Qualified class name of the root component."""
    address: MessageAddress | None
    generation: int
    pending: bool
    finished: bool
    age: float
    """Seconds since the mount was constructed."""
    idle: float
    """Seconds since the initial send or last accepted click — what the timeout counts."""
    expires_in: float | None
    """Seconds of idle timeout left, or `None` for a mount that never times out."""
    lifecycle: MessageRootStatus
    """Whether the application tree or framework renewal generation is visible."""
    handle_expires_in: float | None
    """Seconds of known edit authority left, or `None` for permanent/unknown authority."""
    access: AccessPolicy
    handler_keys: tuple[str, ...]
    """Action keys the live generation answers to."""
    suppressed: int
    """Renders committed without a Discord edit because they matched the live generation."""
    render_cache: RenderProgramCacheSnapshot
    scene: scene.Scene | None
    report: PlanReport | None
    metrics: PlanMetrics | None

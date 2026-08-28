"""Host-facing contracts of a message root: hooks, schedulers, challenges, and expiry.

Everything here is a protocol, policy value, or read-only snapshot that a host implements
or consumes; the behaviour they describe lives in :mod:`squid_ui_discord.message_root`.
"""

import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, Self, TypedDict, Unpack, runtime_checkable

import discord

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.guards import Challenge
from squid_ui.interactions import ActionMiddleware
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.navigation import NavFactory
from squid_ui.profiling import Profiler
from squid_ui.runtime.topics import Address, TopicBus
from squid_ui.scene.model import PlanMetrics, PlanReport
from squid_ui.text import NEUTRAL, Localization, TextLike
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.render_cache import RenderProgramCache, RenderProgramCacheSnapshot
from squid_ui_discord.target import DISCORD_V2_DPY27, Target

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
    """Somewhere to run an approved press that is not the press that approved it.

    The requirement this exists for: `transaction()` flattens rather than nests, so running
    the challenged press from inside the approving handler would run it in the *dialog's*
    transaction -- staging the panel's writes in the dialog's overlay, committing with it,
    and unwinding through its error hook. A `PARALLEL_READ` press would not even misbehave,
    it would raise, because `readonly_transaction()` refuses to nest.

    Spawning a task from the approving handler does not escape it either: the transaction is
    held in a `ContextVar` and a task started there inherits the context. So `resume` must
    hand the work to a task whose own context predates the press -- in practice a queue
    drained by something started at host startup. It is deliberately synchronous: an
    implementation that could await would be tempted to await the press itself.
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
    """The interaction that asked. Its response has been spent on the question, so it is an
    actor identity and a private answering channel -- never a handle to this mount's message."""
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
    """A scheduler backed by a topic bus (see `MessageRootScheduler`).

    Separate from `Scheduler` because following is optional: a mount with no scheduler, or
    one whose scheduler only absorbs refreshes, is simply not live-updated.
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

    def __post_init__(self) -> None:
        _validate_warning(self.warning)


@dataclass(frozen=True, slots=True)
class RenewEphemeral:
    """Replace an expiring ephemeral panel with an explicit renewal screen."""

    warning: float = 90.0
    label: TextLike | None = None

    def __post_init__(self) -> None:
        _validate_warning(self.warning)


type ExpiryPolicy = PauseUpdates | RenewEphemeral


DEFAULT_EXPIRY = PauseUpdates()


def monotonic() -> float:
    """The default clock a message root ages against."""
    return time.monotonic()


class MessageRootOptions(TypedDict, total=False):
    """The keywords that configure a message root, as a forwardable bundle.

    Paired with :class:`MessageRootConfig`, which holds the same set with its defaults. Two
    declarations is the floor: a TypedDict cannot be derived from a dataclass at type-check
    time. `tests/test_sessions.py` pins them against each other, and `access` is in neither
    -- it identifies who may use one specific mount, so it is never a default.
    """

    target: Target
    chrome: Chrome
    localization: Localization
    palette: Palette
    strict: bool
    timeout: float | None
    on_error: ErrorHook | None
    middleware: Sequence[ActionMiddleware]
    profiler: Profiler | None
    render_cache: RenderProgramCache | None
    scheduler: Scheduler | None
    expiry: ExpiryPolicy | None
    nav: NavFactory | None
    challenge: ChallengePresenter | None
    acknowledgement_timeout: float
    pending_after: float
    clock: Callable[[], float]


@dataclass(frozen=True, slots=True)
class MessageRootConfig:
    """Everything a message root is configured with, and what each value defaults to.

    The single home for those defaults: `MessageRoot.__init__` reads them from here rather
    than restating them, and a host that wants different ones builds one of these instead of
    repeating keywords at every construction site.
    """

    target: Target = DISCORD_V2_DPY27
    chrome: Chrome = DEFAULT_CHROME
    localization: Localization = NEUTRAL
    palette: Palette = DEFAULT_PALETTE
    strict: bool = False
    timeout: float | None = 900
    on_error: ErrorHook | None = None
    middleware: Sequence[ActionMiddleware] = ()
    profiler: Profiler | None = None
    render_cache: RenderProgramCache | None = None
    scheduler: Scheduler | None = None
    expiry: ExpiryPolicy | None = DEFAULT_EXPIRY
    nav: NavFactory | None = None
    challenge: ChallengePresenter | None = None
    acknowledgement_timeout: float = 2.5
    pending_after: float = 1.0
    clock: Callable[[], float] = monotonic

    def replace(self, **changes: Unpack[MessageRootOptions]) -> Self:
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
    """One read-only look at a live message root, for host diagnostics.

    A single call rather than a dozen properties: it fixes what a mount is willing to say
    about itself, and a caller cannot accidentally mutate what it reads. Everything here is
    either a scalar or already immutable, so nothing is copied. The deeper payloads — the
    components' declared state and the presentation session — stay behind `runtime` and
    `presentation`, because building them costs more than a list of sessions should.
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

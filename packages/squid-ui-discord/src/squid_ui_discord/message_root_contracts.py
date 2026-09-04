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

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.guards import Challenge
from squid_ui.interactions import ActionMiddleware
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.navigation import NavFactory
from squid_ui.profiling import Profiler
from squid_ui.runtime.topics import Address, TopicBus
from squid_ui.scene.model import PlanMetrics, PlanReport
from squid_ui.target_types import (
    ComponentsV2Target,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    DiscordTarget,
)
from squid_ui.text import NEUTRAL, Localization, TextLike
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.render_cache import RenderProgramCache, RenderProgramCacheSnapshot
from squid_ui_discord.target import DISCORD_V2_DPY27, Target

if TYPE_CHECKING:
    from squid_ui_discord.message_root import AnyMessageRoot


class ErrorHook(Protocol):
    """Host-provided handler for exceptions escaping a component callback.

    The interaction may still be unanswered, so the hook may respond to it. Without a hook the
    mount logs the error and does nothing else.
    """

    def __call__(self, interaction: discord.Interaction, error: Exception, source: str) -> Awaitable[None]:
        """Handle `error`.

        `source` says where it escaped from: `action:<key>`, `form:<key>`, `guard:<key>`,
        `access`, `renewal`, or `item:<discord.py item type>` for a view callback.
        """
        ...


class FinishHook(Protocol):
    """Observer told that a mount has finished, after its teardown."""

    # Positional-only, as `MessageDestination` is: a named parameter would make the protocol demand
    # that every observer spell the argument `mount`.
    def __call__(self, message_root: AnyMessageRoot, /) -> Awaitable[None]:
        """Called once per mount, from every terminal path; an exception is logged and swallowed.

        `message_root.finished` is already true, its view is stopped and its runtime is
        finished. Calling `finish` on it here is a no-op.
        """
        ...


class PresentedHook(Protocol):
    """Observer told that Discord accepted and the mount committed a generation.

    Synchronous on purpose: it runs at the commit point, under the lock every operation
    that can replace the visible message shares, so a hook that could await would be able
    to wait on the mount that is calling it.
    """

    def __call__(self, message_root: AnyMessageRoot, /) -> None:
        """Called under the render lock after each Discord write commits, renewal screens included.

        Not called for a render suppressed as identical to the live one; `CommittedHook` is.
        Must not await or call anything that takes the render lock. An exception is logged
        and swallowed.
        """
        ...


class CommittedHook(Protocol):
    """Observer told that an application render committed its runtime state.

    Synchronous for the same reason as `PresentedHook`: commits run under the shared
    render lock, where awaiting or re-entering the mount would deadlock.
    """

    def __call__(self, message_root: AnyMessageRoot, /) -> None:
        """Called under the render lock after each application render commits, delivered or suppressed.

        Not called for a renewal screen, which commits no runtime state. Must not await or
        call anything that takes the render lock. An exception is logged and swallowed.
        """
        ...


class Scheduler(Protocol):
    """Anything that can absorb out-of-band refresh requests (see `MessageRootScheduler`)."""

    def schedule(self, message_root: AnyMessageRoot) -> None:
        """Arrange for `message_root.refresh()` to run later, in a task the scheduler owns.

        Called from `MessageRoot.schedule` and from bus subscription callbacks. The scheduler
        calls `message_root.invalidate()` before the refresh, ignores a mount whose `finished`
        is true, and may coalesce several requests for one mount into one refresh.
        """
        ...


@runtime_checkable
class ProfiledScheduler(Protocol):
    """A scheduler that carries the profiler its mounts should inherit."""

    @property
    def profiler(self) -> Profiler:
        """Adopted by a mount constructed with this scheduler and no `profiler` of its own."""
        ...


@runtime_checkable
class ReactiveScheduler(Protocol):
    """A scheduler that can preserve the component attribution of a bus change."""

    def schedule_reactive(self, message_root: AnyMessageRoot, address: Address) -> None:
        """Like `Scheduler.schedule`, for a change to one bus `address`.

        Must call `message_root.runtime.invalidate_address(address)` rather than
        `invalidate()`, so only the components that read the address re-render. A mount uses
        this instead of `schedule` whenever its scheduler provides it.
        """
        ...


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

    def resume(self, press: ResumedPress) -> None:
        """Queue `press` to be awaited by a task whose context predates the dialog, and return.

        `press` is a `ChallengeRequest.approve` or `decline`. Application failures inside it
        already go through the mount's error hook; anything that still escapes is the
        supervisor's to log.
        """
        ...


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

    async def present(self, request: ChallengeRequest) -> None:
        """Respond to `request.interaction` with the question, before anything else is awaited.

        That response is the press's only acknowledgement and has the click's 3-second
        deadline; nothing in the mount defers it first. Once the actor answers, hand
        `request.approve` or `request.decline` to a `ChallengeSupervisor` rather than
        awaiting either here. An exception raised here reaches the mount's error hook as
        `guard:<key>`.
        """
        ...


@runtime_checkable
class ExpirySupervisor(Protocol):
    """A scheduler that observes mount edit-authority deadlines."""

    def watch(self, message_root: AnyMessageRoot) -> Callable[[], None]:
        """Start checking `message_root.handle` against its expiry policy; return the stop callback.

        Called once, under the render lock, after the mount's first successful send. On each
        sweep the supervisor asks `message_root._should_arm_expiry(handle, now)` and, once per
        handle it says yes to, calls `message_root._queue_expiry_arm(handle)` then `schedule`.
        The mount calls the returned callback at teardown; calling it twice is harmless.
        """
        ...


@runtime_checkable
class TopicScheduler(Protocol):
    """A scheduler backed by a topic bus (see `MessageRootScheduler`).

    Separate from `Scheduler` because following is optional: a mount with no scheduler, or
    one whose scheduler only absorbs refreshes, is simply not live-updated.
    """

    bus: TopicBus
    """Where a mount subscribes the cell addresses its committed render read."""

    def schedule(self, message_root: AnyMessageRoot) -> None:
        """See `Scheduler.schedule`."""
        ...


def _validate_warning(warning: float) -> None:
    if not math.isfinite(warning) or warning <= 0:
        message = "an expiry warning must be a finite positive number of seconds"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PauseUpdates:
    """Append `Chrome.updates_paused` to the panel before temporary edit authority expires.

    The default policy; it needs no scheduler support beyond `ExpirySupervisor`. Raises
    `ValueError` for a `warning` that is not a finite positive number of seconds.
    """

    warning: float = 60.0
    """Seconds of edit authority left at which the status is shown."""

    def __post_init__(self) -> None:
        _validate_warning(self.warning)


@dataclass(frozen=True, slots=True)
class RenewEphemeral:
    """Replace an expiring ephemeral panel with a single "continue" button that restores it.

    Only an ephemeral message gets the screen; on any other message this policy does nothing.
    A mount built with it requires a scheduler that is an `ExpirySupervisor`. Raises
    `ValueError` for a `warning` that is not a finite positive number of seconds.
    """

    warning: float = 90.0
    """Seconds of edit authority left at which the renewal screen replaces the panel."""
    label: TextLike | None = None
    """Button text, defaulting to `Chrome.continue_session`."""

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


class MessageRootOptions[
    RenderTargetT: DiscordTarget = ComponentsV2Target,
    AdapterT: DiscordPyAdapter = DiscordPy27Adapter,
](MessageRootBehaviorOptions, total=False):
    """Every keyword that configures a message root, as a forwardable bundle.

    Paired with :class:`MessageRootConfig`, which holds the same set with its defaults. Two
    declarations is the floor: a TypedDict cannot be derived from a dataclass at type-check
    time. `tests/test_message_root_options.py` pins them against each other, and `access` is in neither
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
    """Whether a lossy layout adaptation fails the render with `LayoutDegradedError` instead of logging."""
    timeout: float | None = 900
    """Idle seconds, counted from the send or last accepted click, after which the mount finishes."""
    on_error: ErrorHook | None = None
    middleware: Sequence[ActionMiddleware] = ()
    """Outermost first; the same instance listed twice runs once."""
    profiler: Profiler | None = None
    """Falls back to the scheduler's when it is a `ProfiledScheduler`, then to a no-op."""
    render_cache: RenderProgramCache | None = None
    """Shared across mounts when given; a mount that builds its own clears it at finish."""
    scheduler: Scheduler | None = None
    """Without one, `MessageRoot.schedule` refreshes inline and bus changes never reach the mount."""
    expiry: ExpiryPolicy | None = DEFAULT_EXPIRY
    nav: NavFactory | None = None
    """Draws pager controls; `default_nav` when `None`."""
    challenge: ChallengePresenter | None = None
    acknowledgement_timeout: float = 2.5
    """Seconds a handler may run before the click is deferred for it; must lie in (0, 3)."""
    pending_after: float = 1.0
    """Seconds a handler with a `BusySpec` may run before its interim paint appears."""
    clock: Callable[[], float] = monotonic
    """Monotonic seconds; what `timeout` and the snapshot's ages are measured with."""

    def replace(self, **changes: Unpack[MessageRootOptions[RenderTargetT, AdapterT]]) -> Self:
        """`dataclasses.replace`, with the keywords type-checked against `MessageRootOptions`."""
        return replace(self, **changes)


DEFAULT_MESSAGE_ROOT_CONFIG = MessageRootConfig()


class MessageRootStatus(StrEnum):
    """Which mount-owned generation the reader can currently see."""

    ACTIVE = "active"
    """The application tree; its controls dispatch to component handlers."""
    RENEWAL_ARMED = "renewal_armed"
    """The `RenewEphemeral` screen; the tree is retained but hidden until its button is pressed."""


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
    """Whether the mount holds state Discord has not seen yet."""
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
    """The plan on screen, with `report` and `metrics`; all three are `None` before the first commit."""
    report: PlanReport | None
    metrics: PlanMetrics | None

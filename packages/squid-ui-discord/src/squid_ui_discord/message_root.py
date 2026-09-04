"""The mount: one component bound to one Discord message.

Every interaction funnels through :meth:`MessageRoot.dispatch` — access policy, handler, error hook,
and the re-render/edit cycle live here once instead of per view. The mount outlives its
discord.py views: each render produces a fresh :class:`MountedView`, and the previous one is
stopped after a successful edit so dispatch tables do not accumulate.
"""

import asyncio
import logging
import secrets
import time
import weakref
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Any, Unpack, cast, overload

import anyio
import discord

# `discord.ui.select` names the decorator, so the submodule holding `BaseSelect` is only
# reachable by importing from it directly.
from squid_reactivity.actions import ActionContext, ActorRef
from squid_ui import scene
from squid_ui.chrome import CHROME_CONTEXT, LOCALIZATION_CONTEXT, localize_chrome
from squid_ui.document import Asset, Document
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding, FormSpec, FormValidationMode, SubmitHandler
from squid_ui.guards import Challenge, GuardLedger, approvals
from squid_ui.interactions import (
    ActionBinding,
    ActionEvent,
    ActionMiddleware,
    ActionMode,
    ActionProceed,
    ActionRequest,
    Actor,
    EntitySelectionEvent,
    InteractionKind,
    PressEvent,
    SelectionEvent,
    SubmitEvent,
)
from squid_ui.palette import Palette
from squid_ui.planning.adapter import (
    AdapterCapability,
)
from squid_ui.planning.discord import CLASSIC_TARGET_ID, V2_TARGET_ID
from squid_ui.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    NavigationContext,
    NavigationState,
    NavNode,
    default_nav,
)
from squid_ui.planning.planner import plan as plan_document
from squid_ui.planning.target import AnyTarget
from squid_ui.primitives.nodes import Button, Row
from squid_ui.profiling import (
    ActionStatus,
    DispatchDisposition,
    DispatchResult,
    GenerationDecision,
    NoOpProfiler,
    OperationKind,
    OperationRecorder,
    PresentationStatus,
    TraceLink,
    TraceResult,
    TraceStatus,
)

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_ui.runtime.component import Component, ComponentTree
from squid_ui.runtime.histories import History
from squid_ui.runtime.owner import ComponentRuntime
from squid_ui.runtime.presentation_state import PresentationState, apply_updates
from squid_ui.runtime.reactivity import (
    ActionCommit,
    ActionContinuation,
    on_action_commit,
    readonly_transaction,
    transaction,
)
from squid_ui.runtime.resources import AsyncBinding, PendingMode, abandon_superseded_loads
from squid_ui.runtime.topics import Address, SubscriptionReconciler
from squid_ui.scene.model import PlanResult
from squid_ui.semantic import Status
from squid_ui.sources import Position
from squid_ui.target_types import ComponentsV2Target, DiscordPy27Adapter, DiscordPyAdapter, DiscordTarget
from squid_ui.text import Localization, TextLike, localization_scope, resolve_text
from squid_ui_discord import delivery as deliver
from squid_ui_discord import live
from squid_ui_discord.access import AccessPolicy, Allowed, Denied, Owner
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.adapter import require_discord_py_target
from squid_ui_discord.attachments import attachment_assets, files_for
from squid_ui_discord.classic import render_message as render_classic_message
from squid_ui_discord.classic_renderer import ClassicRenderer
from squid_ui_discord.message_payload import MessageMode, MessagePayload
from squid_ui_discord.message_root_candidates import (
    _ADMITTED,
    _REFUSED,
    _Admission,
    _ApplicationCandidate,
    _BusyPaint,
    _Candidate,
    _DispatchProfile,
    _drawn,
    _LifecycleCandidate,
    _PlanEnvironment,
    _PlannedCandidate,
    _RenewalBinding,
    _scene_action_keys,
    _SubmitBinding,
)
from squid_ui_discord.message_root_contracts import (
    DEFAULT_MESSAGE_ROOT_CONFIG,
    ChallengeRequest,
    CommittedHook,
    ExpirySupervisor,
    FinishHook,
    MessageAddress,
    MessageRootBehaviorOptions,
    MessageRootConfig,
    MessageRootSnapshot,
    MessageRootStatus,
    PresentedHook,
    ProfiledScheduler,
    ReactiveScheduler,
    RenewEphemeral,
    TopicScheduler,
)
from squid_ui_discord.message_root_wiring import (
    AnyMountedView,
    ClassicMountedView,
    MountedView,
    _disable_all,
    _EntityValues,
    _SelectionValues,
    _wired_entity_select,
    _WiredButton,
    _WiredSelect,
)
from squid_ui_discord.render_cache import RenderProgramCache
from squid_ui_discord.renderer import MountedRenderer, RoutedItem, RoutedSelectItem, V2Renderer
from squid_ui_discord.rendering import RenderedMessage, render_message
from squid_ui_discord.target import Target

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _DiscordBinding:
    """Everything a Discord dialect decides about a mount, in one value.

    The target decides how to plan and render, the renderer, the view factory, and the message
    mode — and nothing else. Keying on the dialect id says so once, in place of four
    branches that each re-derived the answer from a limits subclass. `MessageMode` keeps its
    own job of describing an *observed* message through `message_mode`; it is no longer inferred.
    """

    render_message: Callable[..., Any]
    mode: MessageMode
    render_capability: AdapterCapability
    renderer: Callable[[MessageRoot[Any, Any], float | None], MountedRenderer[Any]]


_BINDINGS: dict[str, _DiscordBinding] = {
    V2_TARGET_ID: _DiscordBinding(
        render_message=render_message,
        mode=MessageMode.COMPONENTS_V2,
        render_capability=AdapterCapability.RENDER_V2,
        renderer=lambda message_root, timeout: V2Renderer(
            limits=message_root.limits,
            view_factory=lambda: MountedView(message_root, timeout),
            cache=message_root.render_cache,
        ),
    ),
    CLASSIC_TARGET_ID: _DiscordBinding(
        render_message=render_classic_message,
        mode=MessageMode.CLASSIC,
        render_capability=AdapterCapability.RENDER_CLASSIC,
        renderer=lambda message_root, timeout: ClassicRenderer(
            limits=message_root.limits,
            view_factory=lambda: ClassicMountedView(message_root, timeout),
            always_view=True,
            cache=message_root.render_cache,
        ),
    ),
}


def _binding_for(target: AnyTarget) -> _DiscordBinding:
    binding = _BINDINGS.get(target.dialect.id)
    if binding is None:
        known = ", ".join(sorted(_BINDINGS))
        message = f"squid_ui_discord cannot mount dialect {target.dialect.id!r} (known: {known})"
        raise LayoutInvariantError(message)
    return binding


_NOOP_PROFILER = NoOpProfiler()

_MAX_LOAD_PASSES = 16
"""Component/resource tiers one delivery loads through -- not retries.

Each pass loads a tier and renders to reveal the next, so this bounds nesting depth. It
only trips on an `on_load` that keeps embedding freshly unloaded components or a resource
whose settled render reveals another pending atomic resource forever; either is a loop
rather than a deep tree.
"""


def _monotonic() -> float:
    return time.monotonic()


def _needs_load(component: Component[Any]) -> bool:
    """Whether this instance still owes an `on_load` before it may render.

    A component that does not override the hook is never deferred, so a tree declaring no
    loads costs no extra pass.
    """
    return not component._loaded and type(component).on_load is not Component.on_load


def _sole_error(group: BaseExceptionGroup[Any]) -> Exception | None:
    """The one ordinary exception `group` holds, or `None` if it holds anything else."""
    leaves: list[BaseException] = []
    stack: list[BaseException] = list(group.exceptions)
    while stack:
        error = stack.pop()
        if isinstance(error, BaseExceptionGroup):
            stack.extend(error.exceptions)
        else:
            leaves.append(error)
    if len(leaves) == 1 and isinstance(leaves[0], Exception):
        return leaves[0]
    return None


@contextmanager
def _unwrapped() -> Iterator[None]:
    """Let a task group's lone failure through as itself.

    anyio wraps even a single exception, and error routing downstream of a mount is
    `isinstance`-based: a host that answers `AccountNotFoundError` with its own wording would
    show the generic crash card instead. Several failures at once stay a group, which a caller
    branching on type should catch with `except*`.
    """
    try:
        yield
    except BaseExceptionGroup as group:
        if (sole := _sole_error(group)) is not None:
            raise sole from None
        raise


type AnyMessageRoot = MessageRoot[Any, Any]
"""A mount for any render target and adapter.

`MessageRoot`'s type parameters both default, so a bare `MessageRoot` annotation silently means
`MessageRoot[ComponentsV2Target, DiscordPy27Adapter]` and rejects every other instantiation —
including `Self` inside `MessageRoot`'s own methods. Internal machinery that works for any mount
takes this instead; public signatures keep the precise generic.
"""


def _unique_by_identity(middleware: Sequence[ActionMiddleware]) -> tuple[ActionMiddleware, ...]:
    """Freeze middleware while treating the same installed instance as idempotent."""
    unique: list[ActionMiddleware] = []
    for candidate in middleware:
        if not any(existing is candidate for existing in unique):
            unique.append(candidate)
    return tuple(unique)


def _wall_clock_now(scheduler: object) -> datetime:
    """The scheduler's own clock if it carries one, otherwise the real one.

    A test scheduler exposes `clock` so expiry can be driven deterministically; the production
    one does not. The result is checked rather than trusted, because `getattr` on an arbitrary
    scheduler says nothing about what it returns.
    """
    clock = getattr(scheduler, "clock", None)
    if not callable(clock):
        return datetime.now(UTC)
    value = clock()
    if not isinstance(value, datetime):
        message = "scheduler clock did not return a datetime"
        raise TypeError(message)
    return value


_SCHEDULED_REFRESH: ContextVar[object | None] = ContextVar("squid_ui_discord_scheduled_refresh", default=None)


@dataclass(slots=True)
class _LifecycleHooks:
    """One mount's lifecycle observers, snapshotted per firing so hooks may register hooks.

    A failing observer is logged and swallowed: it must not abort another observer's
    cleanup, nor the mount's own. The finish set fires at most once.
    """

    presented: list[PresentedHook] = dataclass_field(default_factory=list)
    committed: list[CommittedHook] = dataclass_field(default_factory=list)
    finish: list[FinishHook] = dataclass_field(default_factory=list)
    finish_fired: bool = False

    def notify_presented(self, message_root: AnyMessageRoot) -> None:
        """Notify observers after Discord and the matching visible state commit."""
        for hook in tuple(self.presented):
            try:
                hook(message_root)
            except Exception:
                logger.exception("presented hook failed for mount %s", message_root.id)

    def notify_committed(self, message_root: AnyMessageRoot) -> None:
        """Notify observers after a complete application runtime commit."""
        for hook in tuple(self.committed):
            try:
                hook(message_root)
            except Exception:
                logger.exception("committed hook failed for mount %s", message_root.id)

    async def run_finish(self, message_root: AnyMessageRoot) -> None:
        # Snapshotted because a hook may register another, which belongs to no firing.
        if self.finish_fired:
            return
        self.finish_fired = True
        for hook in tuple(self.finish):
            try:
                await hook(message_root)
            except Exception:
                logger.exception("finish hook failed for mount %s", message_root.id)


class MessageRoot[
    RenderTargetT: DiscordTarget = ComponentsV2Target,
    AdapterT: DiscordPyAdapter = DiscordPy27Adapter,
]:
    """Binds a component to a message and owns its whole interaction lifecycle."""

    @overload
    def __init__(
        self: MessageRoot[ComponentsV2Target, DiscordPy27Adapter],
        component: Component[ComponentsV2Target],
        *,
        access: AccessPolicy,
        config: None = None,
        target: None = None,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> None: ...

    @overload
    def __init__(
        self,
        component: Component[RenderTargetT],
        *,
        access: AccessPolicy,
        target: Target[Any, Any, RenderTargetT, AdapterT],
        config: MessageRootConfig[Any, Any] | None = None,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> None: ...

    @overload
    def __init__(
        self,
        component: Component[RenderTargetT],
        *,
        access: AccessPolicy,
        config: MessageRootConfig[RenderTargetT, AdapterT],
        target: None = None,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> None: ...

    def __init__(
        self,
        component: Component[Any],
        *,
        access: AccessPolicy,
        config: MessageRootConfig[Any, Any] | None = None,
        target: AnyTarget | None = None,
        **overrides: Unpack[MessageRootBehaviorOptions],
    ) -> None:
        """Bind a component to a message.

        `config` supplies every value at once -- a host that configures its mounts the same
        way builds one and reuses it -- and keywords override it for this mount. `access` is
        neither, because it names who may use this specific mount.
        """
        resolved_config = cast(
            MessageRootConfig[RenderTargetT, AdapterT],
            DEFAULT_MESSAGE_ROOT_CONFIG if config is None else config,
        )
        if target is not None:
            resolved_config = resolved_config.replace(target=cast(Target[Any, Any, RenderTargetT, AdapterT], target))
        config = resolved_config.replace(**overrides) if overrides else resolved_config
        chrome = config.chrome
        localization = config.localization
        nav = config.nav
        scheduler = config.scheduler
        expiry = config.expiry
        clock = config.clock
        acknowledgement_timeout = config.acknowledgement_timeout
        target = config.target
        self.config = config
        """What this mount was configured with, for a replacement that should match it."""
        self.id = secrets.token_urlsafe(6)
        self.component = cast(Component[RenderTargetT], component)
        self.clock = clock
        # Diagnostics only. `_active` is what the idle timeout counts from: the initial send
        # and each accepted click move it, while unattended refreshes deliberately do not.
        self._born = self._active = self.clock()
        self.address: MessageAddress | None = None
        """Where this mount's message is, once it has one. Read `handle` to write to it."""
        self._chrome = chrome
        self.localization = localization
        self.palette = config.palette
        self.chrome = localize_chrome(chrome, localization)
        self.nav = nav if nav is not None else default_nav

        def planned_nav(state: NavigationState) -> Sequence[NavNode]:
            async def previous(_event: PressEvent) -> None:
                await self._move_cursor(state.key, -1)

            async def next_(_event: PressEvent) -> None:
                await self._move_cursor(state.key, 1)

            async def seek(page: int) -> None:
                self._seek_cursor(state.key, page)

            return self.nav(NavigationContext(state, previous, next_, seek))

        self._planned_nav = planned_nav
        self.runtime = ComponentRuntime(
            component,
            on_invalidate=self._mark_dirty,
            context={
                CHROME_CONTEXT: self.chrome,
                LOCALIZATION_CONTEXT: localization,
                NAV_FACTORY_CONTEXT: self.nav,
            },
        )
        if not 0 < acknowledgement_timeout < 3:
            message = "a mount acknowledgement timeout must be greater than zero and below Discord's 3-second limit"
            raise ValueError(message)
        self.acknowledgement_timeout = acknowledgement_timeout
        self.pending_after = config.pending_after
        """How long an action carrying `BusySpec` may run before its interim paint appears."""
        self.guards = GuardLedger(now=clock)
        """Where stateful guards keep their counts; it lives and dies with this mount."""
        self.challenge = config.challenge
        """Who shows a guard's challenge and runs the press the actor approves, if anyone."""
        self.target = target
        """The message mode this mount owns for its whole life.

        A mount has one target: changing it means opening a replacement mount, not swapping
        a live message root's renderer out from under its action bindings.
        """
        self.limits = target.limits
        self._binding = _binding_for(target)
        self.mode = self._binding.mode
        require_discord_py_target(target, self._binding.render_capability, "mount this message mode")
        require_discord_py_target(target, AdapterCapability.DISPATCH, "dispatch mounted interactions")
        require_discord_py_target(target, AdapterCapability.INTERACTION_DELIVERY, "deliver mounted interactions")
        """Which kind of Discord message this mount owns, for its whole life."""
        self.strict = config.strict
        self.timeout = config.timeout
        self.retain_routed_on_timeout = config.retain_routed_on_timeout
        self.access = access
        self.on_error = config.on_error
        self._middleware = _unique_by_identity(config.middleware)
        if (profiler := config.profiler) is not None:
            self.profiler = profiler
        elif isinstance(scheduler, ProfiledScheduler):
            self.profiler = scheduler.profiler
        else:
            self.profiler = _NOOP_PROFILER
        self._owns_render_cache = (render_cache := config.render_cache) is None
        self.render_cache = render_cache if render_cache is not None else RenderProgramCache()
        self.scheduler = scheduler
        topic_bus = scheduler.bus if isinstance(scheduler, TopicScheduler) else None
        reconciler_ref: weakref.ReferenceType[SubscriptionReconciler] | None = None

        def collected(_reference: weakref.ReferenceType[AnyMessageRoot]) -> None:
            if reconciler_ref is not None and (reconciler := reconciler_ref()) is not None:
                reconciler.close()

        message_root_ref = weakref.ref(self, collected)

        def refresh(address: Address) -> None:
            if (current := message_root_ref()) is None:
                if reconciler_ref is not None and (reconciler := reconciler_ref()) is not None:
                    reconciler.close()
                return
            if current.scheduler is not None:
                if isinstance(current.scheduler, ReactiveScheduler):
                    current.scheduler.schedule_reactive(current, address)
                else:
                    current.runtime.invalidate_address(address)
                    current.scheduler.schedule(current)

        self._subscriptions = SubscriptionReconciler(topic_bus, refresh)
        reconciler_ref = weakref.ref(self._subscriptions)
        if isinstance(expiry, RenewEphemeral) and not isinstance(scheduler, ExpirySupervisor):
            message = "RenewEphemeral requires a scheduler that supervises mount expiry"
            raise TypeError(message)
        self.expiry = expiry
        self.status: TextLike | None = None
        """Framework-drawn status appended to the document until the next accepted interaction."""
        self._handle: deliver.EditHandle | None = None
        self._delete_handle: deliver.DeleteHandle | None = None
        self._ephemeral: bool | None = None
        self._lifecycle = MessageRootStatus.ACTIVE
        self._unwatch_expiry: Callable[[], None] | None = None
        self._expiry_arm_requested: deliver.EditHandle | None = None
        self._view: AnyMountedView | None = None
        self._handlers: dict[str, ActionBinding] = {}
        # What each render-declared form key presents right now. Separate from `_handlers`,
        # which holds the button that *opens* the form under the very same key.
        self._form_bindings: Mapping[str, FormBinding] = {}
        self._action_lock = asyncio.Lock()
        # Every operation that can replace what Discord shows shares this lock. Handler
        # execution stays outside it; only staged generations and terminal teardown serialize.
        self._render_lock = asyncio.Lock()
        self._generation = 0
        # Generations handed to staged renders: a candidate whose delivery failed must not
        # hand its control ids to the next one.
        self._issued = 0
        self._pending: _Candidate[RenderTargetT] | None = None
        self._dirty = False
        self._settlement_wake: asyncio.Event | None = None
        # Renders committed without a Discord edit because the reader already had them.
        self._suppressed = 0
        self._finished = False
        self._hooks = _LifecycleHooks()
        self._assets: tuple[Asset, ...] = ()
        self._plan: PlanResult | None = None
        self._planned_tree: ComponentTree[RenderTargetT] | None = None
        self._planned_environment: _PlanEnvironment | None = None
        self._document_tree: ComponentTree[RenderTargetT] | None = None
        self._document_status: TextLike | None = None
        self._document: Document[RenderTargetT] | None = None
        # What the committed generation read, what its single staged successor read, and what
        # this mount managed to subscribe to are three different things. A mount with no bus
        # still knows what it is looking at, which lets its own writes repaint it. A follow is
        # acquired at stage time because a write landing between the read and subscription is
        # lost, but only a delivered render may retire one.
        self._follow_warned = False

    @property
    def handle(self) -> deliver.EditHandle | None:
        """How this mount can write to its message right now, if it still can."""
        return self._handle

    async def adopt_handle(self, handle: deliver.EditHandle) -> None:
        """Retain newly established edit authority for this mount's existing message.

        Frontends use this after trading temporary delivery credentials for durable ones.
        Adoption shares the render lock with every Discord write, rejects already-stale
        authority, and never replaces permanent authority with a temporary handle.

        The caller is responsible for establishing that ``handle`` addresses this mount's
        message; :class:`EditHandle` deliberately exposes capability, not coordinates.

        Raises:
            RuntimeError: The mount has already finished.
            StaleHandleError: The supplied handle is already expired.
        """
        async with self._render_lock:
            if self._finished:
                message = f"mount {self.id} has already finished"
                raise RuntimeError(message)
            if handle.expired():
                message = "cannot adopt an expired edit handle"
                raise deliver.StaleHandleError(message)
            if self._handle is not None and self._handle.permanent and not handle.permanent:
                return
            self._handle = handle
            if handle.permanent:
                self._ephemeral = False
            if self._lifecycle is MessageRootStatus.RENEWAL_ARMED:
                candidate: _Candidate[RenderTargetT] | None = None
                try:
                    candidate = cast(_Candidate[RenderTargetT], await self._stage_loaded())
                    wrote = await self._deliver(candidate, through=handle)
                except Exception:
                    if candidate is not None:
                        self._rollback(candidate)
                    raise
                if wrote is None:
                    self._rollback(candidate)
                    return
                self._commit_presented(candidate)
                await self._settle_visible(candidate, through=handle)

    @property
    def pending(self) -> bool:
        """Whether a render is staged that Discord has not seen."""
        return self._dirty

    @property
    def observed(self) -> tuple[Address, ...]:
        """The shared cell addresses the generation on screen read.

        What the reader is looking at, whether or not anything can notify this mount about
        them. A render staged since is not here until it is delivered; `followed` may
        already cover it, because a subscription is acquired early and retired late.
        """
        return self._subscriptions.committed

    @property
    def followed(self) -> tuple[Address, ...]:
        """The bus addresses this mount is subscribed to, for diagnostics.

        `observed`, minus everything a scheduler that cannot follow topics could not
        subscribe to. A host that followed a topic of its own through the scheduler holds that
        subscription itself and is not listed here.
        """
        return self._subscriptions.followed

    @property
    def middleware(self) -> tuple[str, ...]:
        """Qualified action-middleware identities in effective execution order."""
        return tuple(f"{type(candidate).__module__}.{type(candidate).__qualname__}" for candidate in self._middleware)

    @property
    def generation(self) -> int:
        """The live render generation used to reject stale interactions."""
        return self._generation

    @property
    def finished(self) -> bool:
        """Whether this mount has stopped dispatching, by close, timeout or error."""
        return self._finished

    @property
    def plan(self) -> PlanResult | None:
        """The plan behind the generation currently on screen, if one has been committed.

        The resolved scene, the adaptation report and the planner metrics for what the reader
        is actually looking at — as opposed to what a fresh render would produce now.
        """
        return self._plan

    def snapshot(self) -> MessageRootSnapshot:
        """Describe this mount for a diagnostics surface. See :class:`MessageRootSnapshot`."""
        now = self.clock()
        idle = now - self._active
        component = type(self.component)
        handle_expires_in = None
        if self._handle is not None and not self._handle.permanent and self._handle.expires_at is not None:
            current = _wall_clock_now(self.scheduler)
            handle_expires_in = max(0.0, (self._handle.expires_at - current).total_seconds())
        return MessageRootSnapshot(
            id=self.id,
            component=f"{component.__module__}.{component.__qualname__}",
            address=self.address,
            generation=self._generation,
            pending=self._dirty,
            finished=self._finished,
            age=now - self._born,
            idle=idle,
            expires_in=None if self.timeout is None else max(0.0, self.timeout - idle),
            lifecycle=self._lifecycle,
            handle_expires_in=handle_expires_in,
            access=self.access,
            handler_keys=tuple(sorted(self._handlers)),
            suppressed=self._suppressed,
            render_cache=self.render_cache.snapshot(),
            scene=None if self._plan is None else self._plan.scene,
            report=None if self._plan is None else self._plan.report,
            metrics=None if self._plan is None else self._plan.metrics,
        )

    def _remaining_timeout(self) -> float | None:
        if self.timeout is None:
            return None
        return max(0.0, self.timeout - (self.clock() - self._active))

    def _queue_expiry_arm(self, handle: deliver.EditHandle) -> None:
        """Retain the handle identity a scheduler wants rechecked under the render lock."""
        self._expiry_arm_requested = handle

    def _should_arm_expiry(self, handle: deliver.EditHandle, now: datetime) -> bool:
        """Whether current delivery and timeout facts permit this handle's policy UI."""
        policy = self.expiry
        if (
            policy is None
            or self._finished
            or self._lifecycle is not MessageRootStatus.ACTIVE
            or self._plan is None
            or handle is not self._handle
            or handle.permanent
            or handle.expires_at is None
            or (isinstance(policy, RenewEphemeral) and self._ephemeral is not True)
        ):
            return False
        remaining = (handle.expires_at - now).total_seconds()
        timeout = self._remaining_timeout()
        return remaining <= policy.warning and (timeout is None or timeout > remaining)

    async def _apply_expiry_arm(self, profile: OperationRecorder) -> PresentationStatus | None:
        """Apply queued policy UI, returning a status when renewal consumed the refresh."""
        requested = self._expiry_arm_requested
        self._expiry_arm_requested = None
        if requested is None:
            return None
        policy = self.expiry
        now = _wall_clock_now(self.scheduler)
        if not self._should_arm_expiry(requested, now) or requested.expired():
            return None
        assert policy is not None
        if isinstance(policy, RenewEphemeral):
            candidate = self._draw_renewal(profile=profile)
            try:
                wrote = await self._deliver(candidate, files=False, profile=profile)
            except Exception:
                candidate.view.stop()
                self._dirty = True
                raise
            if wrote is None:
                candidate.view.stop()
                self._dirty = True
                logger.debug("mount %s could not arm renewal before its edit handle expired", self.id)
                return PresentationStatus.ABANDONED
            self._commit_renewal(candidate)
            return PresentationStatus.WRITTEN
        self.status = self.chrome.updates_paused
        self.invalidate()
        return None

    def _note_address(self, message: discord.Message | None) -> None:
        """Remember where this mount's message is, the first time Discord says.

        Coordinates never change for a given mount, so this is set once and kept. It reads
        only what a `Message` already carries, and a `None` from an interaction that has no
        message simply leaves the mount unlocated.
        """
        if message is None or self.address is not None:
            return
        self.address = MessageAddress(
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=None if message.guild is None else message.guild.id,
            jump_url=message.jump_url,
            ephemeral=bool(message.flags.ephemeral),
        )

    # --- Rendering ---------------------------------------------------------------------

    def _stage_view(self, *, disabled: bool = False) -> AnyMountedView:
        """Stage a render of the component's current state into a fresh view, committing none of it.

        Private, because a staged generation is not the mount's state: nothing here moves
        handlers, lifecycle hooks, page positions or the live generation, so handing one to a
        send path shows Discord a generation the mount does not own. Delivery goes through
        :meth:`send` or :meth:`flush`, which stage their own render and commit it; a view
        staged here and never delivered is superseded by the next one.
        """
        pending = self._pending
        if pending is not None:
            pending.view.stop()
            self._subscriptions.discard()
        candidate = self._stage(disabled=disabled)
        self._pending = candidate
        return candidate.view

    def _stage(self, *, disabled: bool = False) -> _Candidate[RenderTargetT]:
        """Render and draw one candidate generation, publishing none of it.

        Runs no `on_load`, because it cannot: the paths that can await one stage through
        :meth:`_stage_loaded`, and the terminal and stage-only paths deliberately do not.
        """
        return self._draw(self.runtime.render(), disabled=disabled)

    def _document_for(self, tree: ComponentTree[RenderTargetT]) -> Document[RenderTargetT]:
        """Build the authored document once for an identical runtime tree and status."""
        if tree is self._document_tree and self.status == self._document_status and self._document is not None:
            return self._document
        nodes = tree.nodes if self.status is None else (*tree.nodes, Status(self.status))
        document = Document(nodes, tree.assets, tree.document_key)
        self._document_tree = tree
        self._document_status = self.status
        self._document = document
        return document

    def _plan_environment(self) -> _PlanEnvironment:
        return _PlanEnvironment(
            self.target,
            self._chrome,
            self.localization,
            self.palette,
            self.strict,
            self.nav,
            self.status,
            self.presentation.revision,
        )

    def _plan_tree(
        self, tree: ComponentTree[RenderTargetT], profile: OperationRecorder | None
    ) -> _PlannedCandidate[RenderTargetT]:
        """Plan one complete component tree without constructing Discord objects."""
        context = profile.span("planner") if profile is not None else nullcontext(None)
        with context as planner_span:
            result = plan_document(
                cast(Document[RenderTargetT], self._document_for(tree)),
                target=self.target,
                chrome=self._chrome,
                localization=self.localization,
                palette=self.palette,
                strict=self.strict,
                nav=self._planned_nav,
                session=self.presentation,
                cache=self.runtime.plan_cache,
                memo=self.runtime.plan_memo,
            )
            if planner_span is not None:
                planner_span.set_attribute("cache_hit", result.metrics.cache_hit)
                planner_span.set_attribute("reuse", result.metrics.reuse.value)
                planner_span.set_attribute("states_explored", result.metrics.states_explored)
                planner_span.set_attribute("search_fallback", result.metrics.search_fallback)
        if profile is not None:
            profile.increment("planner.calls")
            profile.increment("planner.cache_hits", int(result.metrics.cache_hit))
            profile.increment("planner.search_fallbacks", int(result.metrics.search_fallback))
            profile.increment("planner.states_explored", result.metrics.states_explored)
        if result.report.events:
            logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
        handlers: dict[str, ActionBinding] = {}
        for key in _scene_action_keys(result.scene):
            binding = result.bindings.get(key)
            if binding is None:
                message = f"scene action {key!r} has no binding"
                raise LayoutInvariantError(message)
            handlers[key] = binding
        return _PlannedCandidate(
            result,
            tree,
            handlers,
            result.form_bindings,
            self.runtime.revision,
            attachment_assets(result),
            result.session_updates,
        )

    def _stage_observations(self, tree: ComponentTree[RenderTargetT]) -> None:
        observed = tree.observations
        if observed and self._subscriptions.bus is None and not self._follow_warned:
            self._follow_warned = True
            logger.warning(
                "mount %s renders shared state or a watched topic but its scheduler has no "
                "topic bus, so changes made elsewhere will not refresh it",
                self.id,
            )
        self._subscriptions.stage(observed)

    def _draw(
        self,
        tree: ComponentTree[RenderTargetT],
        *,
        disabled: bool = False,
        profile: OperationRecorder | None = None,
        planned: _PlannedCandidate[RenderTargetT] | None = None,
        subscriptions_staged: bool = False,
    ) -> _Candidate[RenderTargetT]:
        """Plan and draw one rendered tree into a candidate generation."""
        if not subscriptions_staged:
            self._stage_observations(tree)
        try:
            staged = planned if planned is not None else self._plan_tree(tree, profile)
        except BaseException:
            self._subscriptions.discard()
            raise
        self._issued += 1
        generation = self._issued
        handlers: dict[str, ActionBinding] = {}

        def draw() -> tuple[AnyMountedView, RenderedMessage[Any, Any]]:
            handlers.clear()

            def wire(
                node: scene.Button | scene.Select | scene.EntitySelect, binding: ActionBinding
            ) -> discord.ui.Item[Any]:
                key = binding.key
                handlers[key] = binding
                if isinstance(node, scene.Button):
                    item: discord.ui.Item[Any] = _WiredButton(node, self, key, generation)
                elif isinstance(node, scene.EntitySelect):
                    item = _wired_entity_select(node, self, key, generation)
                else:
                    item = _WiredSelect(node, self, key, generation)
                if disabled:
                    item.disabled = True  # pyrefly: ignore  # both wired types have the attribute
                return item

            renderer = self._renderer(self._remaining_timeout())
            before = self.render_cache.snapshot()
            context = profile.span("renderer") if profile is not None else nullcontext(None)
            with context as renderer_span:
                presentation = renderer.draw(staged.plan.scene, plan=staged.plan, wire=wire)
                after = self.render_cache.snapshot()
                hit = after.hits > before.hits
                if renderer_span is not None:
                    renderer_span.set_attribute("program_cache_hit", hit)
            if profile is not None:
                profile.increment("renderer.program_hits", int(hit))
                profile.increment("renderer.program_misses", int(not hit))
            rendered = RenderedMessage(presentation, staged.plan)
            view = rendered.payload.view
            if not isinstance(view, MountedView | ClassicMountedView):
                message = "mounted Discord renderer returned the wrong view type"
                raise TypeError(message)
            return view, rendered

        try:
            view, rendered = draw()
        except BaseException:
            self._subscriptions.discard()
            raise
        assets = rendered.assets
        if handlers.keys() != staged.handlers.keys() or assets != staged.assets:
            self._subscriptions.discard()
            view.stop()
            message = "Discord drawing changed the planned action or attachment shape"
            raise LayoutInvariantError(message)
        if disabled:
            _disable_all(view)
        return _Candidate(
            view,
            rendered,
            tree,
            handlers,
            rendered.plan.form_bindings,
            generation,
            self.runtime.revision,
            assets,
            rendered.plan.session_updates,
        )

    def _preflight(
        self,
        tree: ComponentTree[RenderTargetT],
        *,
        profile: OperationRecorder | None = None,
    ) -> _ApplicationCandidate[RenderTargetT]:
        """Plan first, returning an undrawn candidate only when the live scene already matches."""
        self._stage_observations(tree)
        if (
            tree is self._planned_tree
            and self._plan is not None
            and self._lifecycle is MessageRootStatus.ACTIVE
            and self._planned_environment == self._plan_environment()
        ):
            if profile is not None:
                profile.increment("planner.owner_hits")
            return _PlannedCandidate(
                self._plan,
                tree,
                dict(self._handlers),
                self._form_bindings,
                self.runtime.revision,
                self._assets,
                (),
            )
        try:
            planned = self._plan_tree(tree, profile)
        except BaseException:
            self._subscriptions.discard()
            raise
        if self._same_as_live(planned):
            return planned
        return self._draw(tree, profile=profile, planned=planned, subscriptions_staged=True)

    def _draw_renewal(self, *, disabled: bool = False, profile: OperationRecorder | None = None) -> _LifecycleCandidate:
        """Plan the compact framework-owned renewal generation without rendering the component."""
        policy = self.expiry
        if not isinstance(policy, RenewEphemeral):
            message = "a renewal screen requires RenewEphemeral policy"
            raise TypeError(message)
        self._issued += 1
        generation = self._issued
        # Declared as the base binding because that is what `_LifecycleCandidate` carries and
        # what `_handlers` is; `dict` is invariant, so the narrower element type would not
        # assign even though every value written here is a `_RenewalBinding`.
        handlers: dict[str, ActionBinding] = {}

        async def renew_marker(event: PressEvent) -> None:
            # Dispatch recognizes the binding type and never invokes this marker.
            return None

        label = self.chrome.continue_session if policy.label is None else self._chrome_text(policy.label)
        document = Document(
            (
                Status(self.chrome.session_expiring),
                Row((Button(label, renew_marker, "__squid_continue_session"),)),
            )
        )

        def wire(
            node: scene.Button | scene.Select | scene.EntitySelect, binding: ActionBinding
        ) -> discord.ui.Item[Any]:
            if not isinstance(node, scene.Button):
                message = "the renewal generation may only contain its framework button"
                raise TypeError(message)
            internal = _RenewalBinding(
                binding.key,
                binding.handler,
                binding.mode,
                binding.routes,
                binding.guard,
                binding.busy,
            )
            handlers[internal.key] = internal
            item = _WiredButton(node, self, internal.key, generation)
            if disabled:
                item.disabled = True
            return item

        rendered = self._render_message()(
            document,
            wire=wire,
            renderer=self._renderer(self._remaining_timeout()),
            target=self.target,
            chrome=self._chrome,
            localization=self.localization,
            palette=self.palette,
            strict=self.strict,
            nav=lambda state: (),
            session=self.presentation,
            cache=self.runtime.plan_cache,
            memo=self.runtime.plan_memo,
            profile=profile,
        )
        view = rendered.payload.view
        if not isinstance(view, MountedView | ClassicMountedView):
            message = "mounted Discord renderer returned the wrong view type"
            raise TypeError(message)
        if disabled:
            _disable_all(view)
        return _LifecycleCandidate(view, rendered, handlers, generation)

    @contextmanager
    def _action_transaction(self, mode: ActionMode, context: ActionContext) -> Iterator[None]:
        """Run one handler in its transaction, watching for writes this mount renders.

        A shared cell publishes on the bus rather than invalidating a component, so without
        this the mount that *made* the write learns about it the same way a sibling does --
        after the bus drains, one edit later, with the click already answered by a deferral.
        Noticing one's own commit is not a second notification mechanism: there is no
        subscriber index and no back-reference, only the delta the transaction already built.
        """
        if mode is ActionMode.PARALLEL_READ:
            with readonly_transaction():
                yield
            return
        with transaction(action_context=context):
            on_action_commit(self._note_shared_writes, key=self)
            yield

    def _note_shared_writes(self, commit: ActionCommit, continuation: ActionContinuation) -> None:
        """Move the render-input revision if the action wrote a shared cell this mount reads.

        Through `invalidate` rather than `_dirty` directly, because a candidate delivered
        while the handler ran commits `runtime.dirty` over whatever this set: the revision is
        the only dirtiness a render in flight is measured against. Against `_watched` rather
        than `_observed`, because a candidate that newly reads the written cell must not
        commit a value it has already been told is stale.
        """
        watched = self._subscriptions.watched
        if not watched:
            return
        self.runtime.invalidate_addresses(address for address in commit.patches.addresses() if address in watched)

    def _render_message(self) -> Callable[..., RenderedMessage[Any]]:
        """Which plan-and-render entry point this message root's target uses."""
        return self._binding.render_message

    def _renderer(self, timeout: float | None) -> MountedRenderer[Any]:
        return self._binding.renderer(self, timeout)

    def _chrome_text(self, text: TextLike) -> str:
        return resolve_text(text, self.localization).content

    async def _repaint(
        self,
        busy_key: str | None,
        pending: str | None,
        *,
        through: deliver.EditHandle | None,
    ) -> deliver.EditHandle | None:
        """Redraw the scene already on screen, optionally as a busy interim.

        Not a render: the committed plan is drawn again with the same control ids, so the
        panel that comes back is the one the reader was looking at. With `busy_key` the
        pressed button takes `pending` and every control is disabled; without it this is the
        restore. Either way the component tree is untouched, which is the point — the handler
        is mid-transaction and a re-render would observe half-written state.
        """
        async with self._render_lock:
            if self._finished or self._lifecycle is MessageRootStatus.RENEWAL_ARMED:
                return None
            plan = self._plan
            if plan is None:
                return None
            generation = self._generation
            busy = busy_key is not None

            def wire(
                node: scene.Button | scene.Select | scene.EntitySelect, binding: ActionBinding
            ) -> discord.ui.Item[Any]:
                if isinstance(node, scene.Button):
                    if busy:
                        node = replace(
                            node,
                            disabled=True,
                            label=pending if binding.key == busy_key and pending is not None else node.label,
                        )
                    return _WiredButton(node, self, binding.key, generation)
                if isinstance(node, scene.EntitySelect):
                    return _wired_entity_select(
                        replace(node, disabled=True) if busy else node, self, binding.key, generation
                    )
                return _WiredSelect(replace(node, disabled=True) if busy else node, self, binding.key, generation)

            # No timeout on the paint: the committed view still owns the mount's idle timer,
            # and a second one would race it. For the same reason the paint is never
            # `stop()`ed -- it shares the committed generation's custom ids, so unregistering
            # it would take the live controls' dispatch entries with it.
            presentation = self._renderer(None).draw(plan.scene, plan=plan, wire=wire)
            if busy and presentation.view is not None:
                _disable_all(presentation.view)
            return await self._write(presentation, keep_attachments=True, through=through)

    def _commit(self, candidate: _Candidate[RenderTargetT]) -> None:
        """Publish a delivered candidate — the one place a render becomes the mount's state."""
        self._commit_render(candidate)
        self._commit_delivery(candidate)

    def _settle(self, candidate: _ApplicationCandidate[RenderTargetT], ending: str) -> None:
        """Record that `candidate` has reached its one ending, refusing a second.

        The discipline `_Subscriptions.commit`/`discard` already keeps for the reactive
        half, kept here for the visible half.
        """
        if candidate.settled:
            generation = candidate.generation if isinstance(candidate, _Candidate) else "undrawn"
            message = f"mount {self.id}: candidate generation {generation} already settled, cannot {ending}"
            raise LayoutInvariantError(message)
        candidate.settled = True

    def _commit_render(self, candidate: _ApplicationCandidate[RenderTargetT]) -> None:
        """Commit the candidate's application runtime; what the reader sees is untouched.

        `session_updates` apply here too: planning's clamps describe the scene that is on
        screen, and a suppressed candidate is on screen by definition.
        """
        self._settle(candidate, "commit")
        apply_updates(self.presentation, candidate.session_updates)
        self.runtime.plan_memo.promote(self.presentation, self.presentation.revision)
        self._subscriptions.commit()
        self.runtime.commit(candidate.tree, rendered_revision=candidate.revision)
        self._handlers = candidate.handlers
        self._form_bindings = candidate.form_bindings
        self._plan = candidate.plan
        self._planned_tree = candidate.tree
        self._planned_environment = self._plan_environment()
        self._dirty = self.runtime.dirty
        self._pending = None

    def _commit_delivery(self, candidate: _Candidate[RenderTargetT]) -> None:
        """Make the candidate's generation the live one: its control ids now answer clicks."""
        self._generation = candidate.generation
        self._assets = candidate.assets
        self._lifecycle = MessageRootStatus.ACTIVE
        candidate.view.timeout = self._remaining_timeout()
        self._swap_view(candidate.view)
        # The commit point is where a mount becomes something a reader can see and click, and
        # so the first moment it is worth listing as live. Idempotent after the first.
        live.track(self)

    def _same_as_live(self, candidate: _ApplicationCandidate[RenderTargetT]) -> bool:
        """Whether delivering `candidate` would show the reader exactly what is already there.

        Decided at the scene, which is generation-free; control ids are minted at draw time,
        so two presentations of one panel never compare equal. Asset *content* can change
        under the same name, and the visible controls must retain the same logical key set.
        Binding semantics are deliberately excluded: suppression publishes their latest
        values through the mount's key indirection without replacing the live controls.
        """
        plan = self._plan
        if plan is None or self._lifecycle is not MessageRootStatus.ACTIVE:
            return False
        if candidate.plan.report.scene_fingerprint != plan.report.scene_fingerprint:
            return False
        if candidate.assets != self._assets:
            return False
        return candidate.handlers.keys() == self._handlers.keys()

    def _suppress(
        self,
        candidate: _ApplicationCandidate[RenderTargetT],
        profile: OperationRecorder | None,
    ) -> None:
        """Commit a render the reader already has, without an edit and without a new generation.

        The live generation keeps its control ids, so a click already in flight still lands.
        Runtime observers are notified because component state and action semantics advanced;
        presentation observers are not because nothing visible moved.
        """
        self._commit_render(candidate)
        if isinstance(candidate, _Candidate):
            candidate.view.stop()
        self._hooks.notify_committed(self)
        self._suppressed += 1
        if profile is not None:
            profile.increment("mount.suppressed", 1)
        logger.debug("mount %s: render identical to the live generation, edit suppressed", self.id)

    def _commit_presented(self, candidate: _Candidate[RenderTargetT]) -> None:
        """Commit one successfully delivered candidate and notify both observer boundaries."""
        self._commit(candidate)
        self._hooks.notify_committed(self)
        self._hooks.notify_presented(self)

    def _commit_renewal(self, candidate: _LifecycleCandidate) -> None:
        """Publish a renewal generation while retaining the hidden application runtime."""
        self._handlers = candidate.handlers
        self._form_bindings = {}
        self._generation = candidate.generation
        self._plan = candidate.rendered.plan
        self._lifecycle = MessageRootStatus.RENEWAL_ARMED
        candidate.view.timeout = self._remaining_timeout()
        self._swap_view(candidate.view)
        live.track(self)
        self._hooks.notify_presented(self)

    def _rollback(self, candidate: _Candidate[RenderTargetT]) -> None:
        """Discard an undelivered candidate; the message still shows the live generation.

        Nothing to unwind: planning only read the session, so dropping the candidate drops
        its presentation writes with it.
        """
        self._settle(candidate, "roll back")
        candidate.view.stop()
        self._subscriptions.discard()
        self._dirty = True
        if self._pending is candidate:
            self._pending = None

    @property
    def presentation(self) -> PresentationState:
        return self.runtime.presentation

    @presentation.setter
    def presentation(self, value: PresentationState) -> None:
        self.runtime.presentation = value

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self._settlement_wake is not None:
            self._settlement_wake.set()

    def invalidate(self) -> None:
        self.runtime.invalidate()

    def localize(self, localization: Localization) -> None:
        """Change the locale used by the next render of this live message root."""
        self.localization = localization
        self.chrome = localize_chrome(self._chrome, localization)
        self.runtime.set_context(CHROME_CONTEXT, self.chrome)
        self.runtime.set_context(LOCALIZATION_CONTEXT, localization)
        self.invalidate()

    def use_palette(self, palette: Palette) -> None:
        """Change the presentation colours used by the next render."""
        if palette == self.palette:
            return
        self.palette = palette
        self.invalidate()

    async def _move_cursor(self, key: str, delta: int) -> None:
        cursor = self.presentation.cursor(key)
        if 0 <= cursor.position.offset + delta < cursor.extent:
            self.presentation.move_cursor(key, Position(offset=cursor.position.offset + delta))
            self.invalidate()

    def _seek_cursor(self, key: str, page: int) -> None:
        """Jump one cursor to a zero-based page, clamped to what it actually holds."""
        cursor = self.presentation.cursor(key)
        if cursor.position.offset == page:
            return
        self.presentation.move_cursor(key, Position(offset=page))
        self.invalidate()

    def reset_cursor(self, key: str | None = None) -> None:
        """Forget one cursor position, or every position when key is omitted."""
        if key is None:
            self.presentation.reset_cursor()
        else:
            self.presentation.reset_cursor(key)
        self.invalidate()

    def attachment_files(self) -> list[discord.File]:
        """Materialize a fresh Discord file set from the current declarative assets.

        A staged render's assets win, so files fetched alongside a `_stage_view()` belong to
        that render rather than to the generation it will replace.
        """
        return files_for(self._pending.assets if self._pending is not None else self._assets)

    # --- Loading -----------------------------------------------------------------------

    async def _stage_loaded(
        self,
        *,
        disabled: bool = False,
        profile: OperationRecorder | None = None,
        preflight: bool = False,
        reuse_committed: bool = False,
    ) -> _ApplicationCandidate[RenderTargetT]:
        """Stage a candidate whose components and atomic resources are settled.

        One pass per embedding tier: the root is known without rendering anything, and each
        tier's loaded render is what reveals the next. Siblings within a tier load together.
        A tier that still owes loads is never drawn -- only rendered, and only to find out
        who they are -- so an incomplete document is never planned. A tree that declares no
        loads is rendered and drawn exactly once, as it was before this existed.

        Pending atomic resources use the same discovery passes. Their pending render is
        complete but deliberately not drawn or delivered; failed state is settled state.

        A raise leaves every completed load completed, every other one eligible to retry, and
        nothing staged, so the mount is exactly as deliverable as it was.
        """
        for pass_index in range(_MAX_LOAD_PASSES):
            cached_atomic = self.runtime.pending_cached_atomic_resources()
            if cached_atomic:
                if profile is None:
                    await self._settle_resources(cached_atomic)
                else:
                    with profile.span(
                        "resource_settle.atomic",
                        attributes={
                            "count": len(cached_atomic),
                            "pass": pass_index,
                            "source": "cached",
                            "strategy": "direct" if len(cached_atomic) == 1 else "task_group",
                        },
                    ):
                        await self._settle_resources(cached_atomic)
                continue
            if _needs_load(root := self.runtime.root):
                if profile is None:
                    await self._load_all((root,))
                else:
                    with profile.span("component_load", attributes={"count": 1, "pass": pass_index}):
                        await self._load_all((root,))
                continue
            if profile is None:
                tree = self.runtime.render(defer=_needs_load, reuse_committed=reuse_committed)
            else:
                with profile.span("runtime_render", attributes={"pass": pass_index}):
                    tree = self.runtime.render(defer=_needs_load, reuse_committed=reuse_committed)
            if tree.deferred:
                if profile is None:
                    await self._load_all(tree.deferred)
                else:
                    with profile.span(
                        "component_load",
                        attributes={"count": len(tree.deferred), "pass": pass_index},
                    ):
                        await self._load_all(tree.deferred)
                continue
            atomic = self._pending_resources(tree, PendingMode.ATOMIC)
            if atomic:
                if profile is None:
                    await self._settle_resources(atomic)
                else:
                    with profile.span(
                        "resource_settle.atomic",
                        attributes={
                            "count": len(atomic),
                            "pass": pass_index,
                            "source": "discovered",
                            "strategy": "direct" if len(atomic) == 1 else "task_group",
                        },
                    ):
                        await self._settle_resources(atomic)
                continue
            if preflight and not disabled:
                if profile is None:
                    return self._preflight(tree)
                with profile.span("preflight"):
                    return self._preflight(tree, profile=profile)
            if profile is None:
                return self._draw(tree, disabled=disabled)
            with profile.span("draw"):
                return self._draw(tree, disabled=disabled, profile=profile)
        message = f"mount {self.id}: component and resource loading did not settle in {_MAX_LOAD_PASSES} passes"
        raise LayoutInvariantError(message)

    @staticmethod
    def _pending_resources(tree: ComponentTree[RenderTargetT], pending: PendingMode) -> tuple[AsyncBinding, ...]:
        return tuple(binding for binding in tree.async_bindings if binding.pending_mode is pending and binding.pending)

    async def _settle_resources(self, resources: Sequence[AsyncBinding]) -> None:
        """Settle one observed resource tier concurrently under this render operation.

        Both discovery paths funnel through here -- `_stage_loaded`'s atomic tier and
        `_settle_visible`'s progress passes -- so one installation covers both policies.
        `squid_reactivity` owns the supersession, this owns the cancellation, and only
        resources take it: an operation execution shares `AsyncBinding` but is never
        implicitly restarted, and the scope lives inside `Resource._loaded`.
        """
        with abandon_superseded_loads(anyio.CancelScope):
            if len(resources) == 1:
                await resources[0]._load()
                return
            async with anyio.create_task_group() as tasks:
                for resource in resources:
                    tasks.start_soon(resource._load)

    async def _settle_visible(
        self,
        committed: _ApplicationCandidate[RenderTargetT],
        *,
        through: deliver.EditHandle | None = None,
        profile: OperationRecorder | None = None,
    ) -> None:
        """Advance explicit async bindings through progress and terminal paints."""
        if self._lifecycle is MessageRootStatus.RENEWAL_ARMED:
            return
        candidate = committed
        for pass_index in range(_MAX_LOAD_PASSES):
            bindings = self._pending_resources(candidate.tree, PendingMode.EXPLICIT)
            if not bindings or self.runtime.dirty:
                return
            wake = asyncio.Event()
            done = asyncio.Event()
            delivery_open = self._handle is not None and not self._handle.expired()
            if not delivery_open:
                bindings = tuple(binding for binding in bindings if binding.settle_without_delivery)
                if not bindings:
                    self._dirty = True
                    return

            async def settle(
                current_bindings: tuple[AsyncBinding, ...] = bindings,
                current_done: asyncio.Event = done,
                current_wake: asyncio.Event = wake,
            ) -> None:
                try:
                    await self._settle_resources(current_bindings)
                finally:
                    current_done.set()
                    current_wake.set()

            async def reconcile(
                current_done: asyncio.Event = done,
                current_wake: asyncio.Event = wake,
                current_bindings: tuple[AsyncBinding, ...] = bindings,
            ) -> None:
                nonlocal candidate, delivery_open
                while True:
                    await current_wake.wait()
                    current_wake.clear()
                    live_progress = any(
                        binding.reconcile_while_pending and binding.pending for binding in current_bindings
                    )
                    while delivery_open and self.runtime.dirty and (current_done.is_set() or live_progress):
                        presented = await self._present_async_update(candidate, through=through, profile=profile)
                        if presented is None:
                            delivery_open = False
                            break
                        candidate = presented
                    if current_done.is_set():
                        return

            self._settlement_wake = wake
            try:
                context = (
                    profile.span(
                        "resource_settle.visible",
                        attributes={"count": len(bindings), "pass": pass_index},
                    )
                    if profile is not None
                    else nullcontext()
                )
                with context:
                    async with anyio.create_task_group() as tasks:
                        tasks.start_soon(settle)
                        tasks.start_soon(reconcile)
            finally:
                self._settlement_wake = None
            if not delivery_open:
                return
        self._dirty = True
        logger.error(
            "mount %s: explicit async bindings did not settle in %s passes",
            self.id,
            _MAX_LOAD_PASSES,
        )

    async def _present_async_update(
        self,
        committed: _ApplicationCandidate[RenderTargetT],
        *,
        through: deliver.EditHandle | None,
        profile: OperationRecorder | None,
    ) -> _ApplicationCandidate[RenderTargetT] | None:
        """Present the latest coalesced status of async bindings, if it changed the scene."""
        candidate: _ApplicationCandidate[RenderTargetT] | None = None
        try:
            candidate = await self._stage_loaded(profile=profile, preflight=True)
            if self._same_as_live(candidate):
                self._suppress(candidate, profile)
                return candidate
            candidate = _drawn(candidate)
            wrote = await self._deliver(candidate, through=through, profile=profile)
        except Exception:
            if isinstance(candidate, _Candidate):
                self._rollback(candidate)
            elif candidate is not None:
                self._subscriptions.discard()
            logger.exception("mount %s could not deliver an async binding update", self.id)
            return None
        if wrote is None:
            self._rollback(candidate)
            return None
        if profile is None:
            self._commit_presented(candidate)
        else:
            with profile.span("commit"):
                self._commit_presented(candidate)
        return candidate

    async def _load_all(self, components: Sequence[Component[RenderTargetT]]) -> None:
        """Load one tier concurrently. A failure cancels its siblings; the render is doomed."""
        if len(components) == 1:
            # The overwhelmingly common case, and no group to unwrap.
            await self._load_one(components[0])
            return
        with _unwrapped():
            async with anyio.create_task_group() as tasks:
                for component in components:
                    tasks.start_soon(self._load_one, component)

    async def _load_one(self, component: Component[Any]) -> None:
        await component.on_load()
        component._loaded = True

    # --- Lifecycle ---------------------------------------------------------------------

    async def send(self, message_destination: deliver.MessageDestination) -> deliver.SendResult:
        """Deliver this mount's first render through `message_destination`.

        The commit point for an initial send, and the same stage -> deliver -> commit sequence
        `flush` runs for an interaction edit: the host chooses where the message goes, the
        mount owns everything around the call. A message_destination that raises leaves the mount on
        its previous generation with the render still pending, so a second `send` is a clean
        retry.

        The structured result distinguishes a committed delivery, including a handle-less
        one, from a message_destination that deliberately abandoned delivery.
        """
        component = type(self.component)
        name = f"{component.__module__}.{component.__qualname__}"
        with self.profiler.operation(OperationKind.SEND, name=name, attributes={"message_root_id": self.id}) as profile:
            with profile.span("render_lock"):
                await self._render_lock.acquire()
            try:
                if self._finished:
                    profile.set_result(TraceResult(TraceStatus.ABANDONED, presentation=PresentationStatus.ABANDONED))
                    return deliver.Abandoned()
                # A render staged by `_stage_view` and never delivered is superseded, not delivered.
                if self._pending is not None:
                    with profile.span("supersede"):
                        self._pending.view.stop()
                        self._pending = None
                        self._subscriptions.discard()
                # Component on_load and atomic resources settle first. Visible resources
                # deliberately make this the pending paint and settle after it commits.
                candidate = cast(_Candidate[RenderTargetT], await self._stage_loaded(profile=profile))
                try:
                    destination_type = (
                        f"{type(message_destination).__module__}.{type(message_destination).__qualname__}"
                    )
                    with profile.span("discord_write", attributes={"message_destination": destination_type}):
                        result = await message_destination(candidate.payload)
                except deliver.DeliveryAbandoned:
                    logger.debug("mount %s was not delivered: the message_destination abandoned it", self.id)
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    profile.set_result(TraceResult(TraceStatus.ABANDONED, presentation=PresentationStatus.ABANDONED))
                    return deliver.Abandoned()
                except Exception:
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    raise
                self._handle = result.handle
                self._delete_handle = result.delete_handle
                self._ephemeral = result.ephemeral
                if result.message is not None:
                    self._note_address(result.message)
                self._active = self.clock()
                with profile.span("commit"):
                    self._commit_presented(candidate)
                if self._unwatch_expiry is None and isinstance(self.scheduler, ExpirySupervisor):
                    self._unwatch_expiry = self.scheduler.watch(self)
                await self._settle_visible(candidate, profile=profile)
                profile.set_result(TraceResult(TraceStatus.COMPLETED, presentation=PresentationStatus.WRITTEN))
                settled = all(not binding.pending for binding in candidate.tree.async_bindings)
                return deliver.Delivered(result, settled=settled)
            finally:
                self._render_lock.release()

    def _swap_view(self, view: AnyMountedView) -> None:
        if self._view is not None and self._view is not view:
            self._view.stop()
        self._view = view

    async def _deliver(
        self,
        candidate: _Candidate[RenderTargetT] | _LifecycleCandidate,
        *,
        through: deliver.EditHandle | None = None,
        files: bool = True,
        profile: OperationRecorder | None = None,
    ) -> deliver.EditHandle | None:
        """Show a staged render, through `through` when it is usable and the standing handle otherwise.

        Returns the handle that wrote, or `None` when none of them could. Which one it was
        matters: only the interaction's own handle answers the click as a side effect of
        editing, so a caller holding an interaction has to read this to know whether it still
        owes an acknowledgement.

        `files=False` leaves the message's attachments alone; a terminal disable-edit changes
        only the controls, and an empty asset set would otherwise strip them.
        """
        return await self._write(candidate.payload, keep_attachments=not files, through=through, profile=profile)

    async def _write(
        self,
        payload: MessagePayload,
        *,
        keep_attachments: bool,
        through: deliver.EditHandle | None,
        profile: OperationRecorder | None = None,
    ) -> deliver.EditHandle | None:
        """Write one payload through the first usable handle, and say which one that was.

        `keep_attachments` leaves the message's files alone, which is what every edit that
        changes only controls wants.
        """
        for handle in (through, self._handle):
            if handle is None or handle.expired():
                continue
            try:
                if profile is None:
                    await handle.write(payload, keep_attachments=keep_attachments)
                else:
                    source = "interaction" if handle is through else "standing"
                    handle_type = f"{type(handle).__module__}.{type(handle).__qualname__}"
                    with profile.span("discord_write", attributes={"source": source, "handle": handle_type}):
                        await handle.write(payload, keep_attachments=keep_attachments)
            except deliver.StaleHandleError:
                logger.debug("mount %s discarded a stale edit handle", self.id, exc_info=True)
                if handle is self._handle:
                    self._handle = None
                continue
            return handle
        return None

    def _source(self, interaction: discord.Interaction, *, resumed: bool) -> deliver.EditHandle | None:
        """The handle a dispatch may write this mount's message through, if any.

        A resumed press has none. Its interaction came from the panel but has already spent
        its response on the question, so `handle_from` refuses it anyway -- this states the
        rule as the mount's own rather than leaving it to what the presenter happened to do
        with the interaction.
        """
        return None if resumed else deliver.handle_from(interaction)

    def _renew(self, interaction: discord.Interaction, *, resumed: bool = False) -> None:
        """Trade up to the credentials this click carries.

        The bot's own never expire, so a mount holding them keeps them. Anything else is
        worth replacing: each interaction resets the clock, so a mount in use stays writable
        even when its message was only ever writable through the interaction that sent it.

        A resumed press trades nothing: taking the handle would re-address the mount to
        wherever the question was answered, permanently, and outlive the press that did it.
        """
        if self._handle is not None and self._handle.permanent:
            return
        if (fresher := self._source(interaction, resumed=resumed)) is not None:
            self._handle = fresher

    async def dispatch(
        self,
        key: str,
        interaction: discord.Interaction,
        values: _SelectionValues = None,
        *,
        generation: int | None = None,
        resumed: bool = False,
    ) -> None:
        """The funnel: finished check -> access policy -> guard -> handler -> flush.

        `resumed` says an approved challenge started this press. Approval re-enters here
        rather than resuming mid-funnel on purpose: the actor may have lost access and the
        panel may have re-rendered while the dialog was open, so every stage runs again
        against current truth.
        """
        kind = InteractionKind.PRESS if values is None else InteractionKind.SELECTION
        with (
            localization_scope(self.localization),
            self.profiler.operation(
                OperationKind.DISPATCH,
                name=key,
                attributes={
                    "kind": kind.value,
                    "message_root_id": self.id,
                    "resumed": resumed,
                    "actor": interaction.user.id,
                },
            ) as operation,
        ):
            profile = _DispatchProfile(
                operation,
                interaction,
                GenerationDecision(generation, self._generation),
                operation.start_span("acknowledgement"),
                resumed=resumed,
            )
            try:
                if not await self._begin_dispatch(interaction, profile):
                    return
                with operation.span("binding"):
                    binding = self._handlers.get(key)
                if binding is None:
                    # A click raced a re-render that removed the control; acknowledge and move on.
                    profile.presentation = await self.refresh(interaction, profile=profile)
                    profile.finish(DispatchDisposition.MISSING)
                    return
                if isinstance(binding, _RenewalBinding):
                    await self._dispatch_renewal(binding, key, interaction, generation, profile)
                    return
                if isinstance(values, list):
                    with operation.span("selection"):
                        binding = binding.routed(tuple(values))
                    if binding is None:
                        await self._acknowledge(interaction, profile=profile, source="invalid_selection")
                        profile.presentation = PresentationStatus.ACKNOWLEDGED
                        profile.finish(DispatchDisposition.INVALID_SELECTION)
                        return
                    key = binding.key

                async def invoke(current: ActionBinding, rebased: bool, active_generation: int) -> None:
                    await self._invoke(
                        current,
                        key,
                        interaction,
                        values,
                        generation,
                        active_generation,
                        rebased,
                        profile,
                    )

                await self._dispatch_binding(
                    binding,
                    key,
                    interaction,
                    generation,
                    invoke,
                    profile,
                    values=values,
                    rebase=lambda: self._handlers.get(key),
                )
            except anyio.get_cancelled_exc_class():
                profile.action = ActionStatus.CANCELLED
                profile.finish(DispatchDisposition.CANCELLED)
                raise
            except Exception as error:
                profile.presentation = PresentationStatus.FAILED
                profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
                raise

    async def _dispatch_renewal(
        self,
        binding: _RenewalBinding,
        key: str,
        interaction: discord.Interaction,
        generation: int | None,
        profile: _DispatchProfile,
    ) -> None:
        """Restore the latest application tree through a renewal click's fresh authority."""
        source = deliver.handle_from(interaction)
        acknowledge = False
        failure: Exception | None = None
        with profile.operation.span("render_lock"):
            await self._render_lock.acquire()
        try:
            if (
                source is None
                or source.expired()
                or self._finished
                or self._lifecycle is not MessageRootStatus.RENEWAL_ARMED
                or generation != self._generation
                or self._handlers.get(key) is not binding
            ):
                acknowledge = True
            else:
                # Adopt before staging or responding. A failed restore remains armed but now
                # has the authority needed for another attempt.
                self._handle = source
                candidate: _Candidate[RenderTargetT] | None = None
                try:
                    candidate = cast(_Candidate[RenderTargetT], await self._stage_loaded(profile=profile.operation))
                    wrote = await self._deliver(candidate, through=source, profile=profile.operation)
                except Exception as error:
                    if candidate is not None:
                        with profile.operation.span("rollback"):
                            self._rollback(candidate)
                    failure = error
                else:
                    if wrote is None:
                        self._rollback(candidate)
                        acknowledge = True
                    else:
                        with profile.operation.span("commit"):
                            self._commit_presented(candidate)
                        await self._settle_visible(candidate, through=source, profile=profile.operation)
                        profile.presentation = PresentationStatus.WRITTEN
                        if wrote is source:
                            profile.acknowledge("interaction_write")
                        acknowledge = wrote is not source
        finally:
            self._render_lock.release()
        if failure is not None:
            profile.presentation = PresentationStatus.FAILED
            await self.handle_error(interaction, failure, "renewal")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACTION_FAILED, failure)
            return
        if acknowledge:
            await self._acknowledge(interaction, profile=profile, source="renewal")
            if profile.presentation is PresentationStatus.NOT_REQUIRED:
                profile.presentation = PresentationStatus.ACKNOWLEDGED
        profile.finish(DispatchDisposition.COMPLETED)

    async def dispatch_submit(
        self,
        key: str,
        interaction: discord.Interaction,
        spec: FormSpec,
        values: Mapping[str, object],
        handler: SubmitHandler,
        *,
        mode: ActionMode = ActionMode.EXCLUSIVE,
        generation: int | None = None,
        label: TextLike = "",
        record: History | None = None,
    ) -> None:
        """Route a modal submission through the same stale, action-policy, access, and flush funnel.

        Under `REBASE` this resolves the newest render-declared binding for `key`, the way a
        stale click does -- but only when that binding parses the same field keys, since a
        schema that has since changed shape cannot read what the reader actually typed. A form
        presented ad hoc from a handler has no render-time binding, and a trigger the newest
        render dropped has no newer one; both run what the reader submitted, because
        discarding a filled-in form is the worse of the two surprises.
        """
        with (
            localization_scope(self.localization),
            self.profiler.operation(
                OperationKind.DISPATCH,
                name=key,
                attributes={
                    "kind": InteractionKind.SUBMIT.value,
                    "message_root_id": self.id,
                    "actor": interaction.user.id,
                },
            ) as operation,
        ):
            profile = _DispatchProfile(
                operation,
                interaction,
                GenerationDecision(generation, self._generation),
                operation.start_span("acknowledgement"),
            )
            try:
                if not await self._begin_dispatch(interaction, profile):
                    return
                binding = _SubmitBinding(key, handler, mode, label=label, record=record, spec=spec)

                def rebase() -> ActionBinding | None:
                    newest = self._form_bindings.get(key)
                    if newest is None or newest.spec.field_keys != spec.field_keys:
                        return binding
                    return _SubmitBinding(
                        key,
                        newest.on_submit,
                        mode,
                        label=newest.label,
                        record=newest.record,
                        spec=newest.spec,
                    )

                async def invoke(current: ActionBinding, rebased: bool, active_generation: int) -> None:
                    resolved = (
                        current.spec if isinstance(current, _SubmitBinding) and current.spec is not None else spec
                    )
                    await self._invoke_submit(
                        current,
                        key,
                        interaction,
                        resolved,
                        values,
                        generation,
                        active_generation,
                        rebased,
                        profile,
                    )

                await self._dispatch_binding(
                    binding,
                    key,
                    interaction,
                    generation,
                    invoke,
                    profile,
                    rebase=rebase,
                )
            except anyio.get_cancelled_exc_class():
                profile.action = ActionStatus.CANCELLED
                profile.finish(DispatchDisposition.CANCELLED)
                raise
            except Exception as error:
                profile.presentation = PresentationStatus.FAILED
                profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
                raise

    async def _begin_dispatch(self, interaction: discord.Interaction, profile: _DispatchProfile) -> bool:
        # A mount sent through an unwaited interaction response never saw its own message; the
        # click is where it finally learns where it lives. A resumed press is not that click:
        # what it carries is where the question was answered.
        if not profile.resumed:
            self._note_address(interaction.message)
        if self._finished:
            # A finished mount can still be on screen with live controls: its disable-edit
            # may have failed, or a replacement may have taken over the session while this
            # message stayed visible. Say so rather than running a handler against state
            # nobody will see again.
            text = resolve_text(self.chrome.session_ended, self.localization).content
            await deliver.respond_text(interaction, text, ephemeral=True)
            profile.presentation = PresentationStatus.WRITTEN
            profile.acknowledge("message_root_finished")
            profile.finish(DispatchDisposition.MESSAGE_ROOT_FINISHED)
            return False
        try:
            with profile.operation.span(
                "access",
                attributes={"policy": f"{type(self.access).__module__}.{type(self.access).__qualname__}"},
            ):
                decision = await self.access.check(interaction)
        except Exception as error:
            await self.handle_error(interaction, error, "access")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACCESS_FAILED, error)
            return False
        if isinstance(decision, Denied):
            reason = self.chrome.not_yours if decision.reason is None else decision.reason
            text = resolve_text(reason, self.localization).content
            await deliver.respond_text(interaction, text, ephemeral=True)
            profile.presentation = PresentationStatus.WRITTEN
            profile.acknowledge("access_denied")
            profile.finish(DispatchDisposition.ACCESS_DENIED)
            return False
        if not isinstance(decision, Allowed):
            error = TypeError(f"access policy returned unsupported decision {type(decision).__name__}")
            await self.handle_error(interaction, error, "access")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACCESS_FAILED, error)
            return False
        self._active = self.clock()
        if self.status is not None:
            self.status = None
            self.invalidate()
        return True

    def _event(self, interaction: discord.Interaction, values: _SelectionValues) -> ActionEvent:
        """The portable event one Discord interaction becomes."""
        actor = Actor(str(interaction.user.id), getattr(interaction.user, "display_name", None))
        responder = ActionResponder(interaction, self, () if not isinstance(values, _EntityValues) else values.resolved)
        locale = self.localization.locale
        if values is None:
            return PressEvent(actor, responder, locale, {"frontend": "discord"})
        if isinstance(values, _EntityValues):
            return EntitySelectionEvent(actor, responder, locale, {"frontend": "discord"}, values.refs)
        return SelectionEvent(actor, responder, locale, {"frontend": "discord"}, tuple(values))

    async def _admit(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: _SelectionValues,
        profile: _DispatchProfile,
    ) -> _Admission:
        """Run this action's guard, answering the reader privately when it refuses.

        The access policy has already said this reader may use the panel; the guard says
        whether this press may run now. A denial writes nothing, bumps no generation, and
        opens no transaction -- it costs exactly one ephemeral message. A challenge costs the
        same, and is not admission deferred but admission refused: the press is dropped, and
        approving the question starts a new one.

        Every pass is staged and committed only when it did not end in a question, so the
        guards that ran ahead of a challenge have spent nothing by the time the actor is
        asked. Denial keeps its writes, exactly as it always has.
        """
        guard = binding.guard
        if guard is None:
            return _ADMITTED
        ledger = self.guards.for_action(key).staged()
        try:
            with profile.operation.span(
                "guard",
                attributes={"guard": f"{type(guard).__module__}.{type(guard).__qualname__}"},
            ):
                decision = await guard.admit(self._event(interaction, values), ledger)
        except Exception as error:
            await self.handle_error(interaction, error, f"guard:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.GUARD_FAILED, error)
            return _REFUSED
        if isinstance(decision, Challenge):
            return _Admission(admitted=False, challenge=decision)
        ledger.commit()
        if decision.allowed:
            return _ADMITTED
        reason = decision.reason
        if reason is None:
            reason = (
                self.chrome.not_now if decision.retry_after is None else self.chrome.try_again_in(decision.retry_after)
            )
        await deliver.respond_text(interaction, self._chrome_text(reason), ephemeral=True)
        profile.presentation = PresentationStatus.WRITTEN
        profile.acknowledge("guard_denied")
        profile.finish(DispatchDisposition.GUARD_DENIED)
        return _REFUSED

    async def _present_challenge(
        self,
        challenge: Challenge,
        key: str,
        interaction: discord.Interaction,
        values: _SelectionValues,
        profile: _DispatchProfile,
    ) -> None:
        """Put the guard's question to the actor, outside the action lock, and end this press.

        Presenting through the interaction *is* the acknowledgement, which is why nothing may
        be awaited ahead of it: admission runs before the watchdog starts, so a challenge has
        the click's own three seconds and no safety net.
        """
        presenter = self.challenge
        if presenter is None:
            error = LayoutInvariantError(
                f"the guard on {key!r} challenged this press, but the mount has no challenge presenter"
            )
            await self.handle_error(interaction, error, f"guard:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.GUARD_FAILED, error)
            return
        if key in self._form_bindings:
            # The press that opens a modal cannot be challenged: asking spends the response,
            # and a resumed press has no fresh interaction to open the modal through.
            error = LayoutInvariantError(f"a form trigger cannot be challenged, and the guard on {key!r} did")
            await self.handle_error(interaction, error, f"guard:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.GUARD_FAILED, error)
            return
        actor = str(interaction.user.id)

        async def approve() -> None:
            await self._approve_challenge(key, interaction, values, actor)

        async def decline() -> None:
            await self._decline_challenge(challenge, key, interaction)

        request = ChallengeRequest(self, interaction, challenge, key, approve, decline)
        try:
            with profile.operation.span("challenge"):
                await presenter.present(request)
        except Exception as error:
            await self.handle_error(interaction, error, f"guard:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.GUARD_FAILED, error)
            return
        profile.presentation = PresentationStatus.WRITTEN
        profile.acknowledge("challenge_issued")
        profile.finish(DispatchDisposition.CHALLENGE_ISSUED)

    async def _approve_challenge(
        self,
        key: str,
        interaction: discord.Interaction,
        values: _SelectionValues,
        actor: str,
    ) -> None:
        """Record one approval and re-enter the funnel from the top.

        `generation=None` on purpose: a dialog outlives renders, and the actor confirmed an
        intent rather than a pixel. That is the contract `ActionMode.REBASE` already
        offers, and `EXCLUSIVE` would otherwise reject the press it just asked about.
        """
        ledger = self.guards.for_action(key)
        bucket = approvals(ledger, actor)
        ledger.write(bucket, ledger.read(bucket, 0) + 1)
        await self.dispatch(key, interaction, values, generation=None, resumed=True)

    async def _decline_challenge(
        self,
        challenge: Challenge,
        key: str,
        interaction: discord.Interaction,
    ) -> None:
        """Note the refusal, and say so when the challenge asked for wording.

        There is nothing to undo: the press was never admitted and the approval that would
        have admitted it was never written.
        """
        with self.profiler.operation(
            OperationKind.DISPATCH,
            name=key,
            attributes={
                "kind": InteractionKind.PRESS.value,
                "message_root_id": self.id,
                "resumed": True,
                "actor": interaction.user.id,
            },
        ) as operation:
            if challenge.on_decline is not None:
                await deliver.respond_text(interaction, self._chrome_text(challenge.on_decline), ephemeral=True)
            operation.set_result(
                TraceResult(
                    TraceStatus.COMPLETED,
                    None,
                    DispatchResult(
                        DispatchDisposition.CHALLENGE_DECLINED,
                        ActionStatus.NOT_RUN,
                        PresentationStatus.WRITTEN
                        if challenge.on_decline is not None
                        else PresentationStatus.NOT_REQUIRED,
                        GenerationDecision(None, self._generation),
                    ),
                )
            )

    async def _dispatch_binding(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        generation: int | None,
        invoke: Callable[[ActionBinding, bool, int], Awaitable[None]],
        profile: _DispatchProfile,
        *,
        values: _SelectionValues = None,
        rebase: Callable[[], ActionBinding | None] | None = None,
    ) -> None:
        if binding.mode in {ActionMode.IMMEDIATE, ActionMode.PARALLEL_READ}:
            rebased = False
            profile.decide_generation(self._generation)
            # No lock to be inside for these two, so admission is simply the last gate
            # before the handler, exactly as it is under the other policies.
            admission = await self._admit(binding, key, interaction, values, profile)
            if admission.challenge is not None:
                await self._present_challenge(admission.challenge, key, interaction, values, profile)
                return
            if not admission.admitted:
                return
            await invoke(binding, rebased, self._generation)
            return

        with profile.operation.span("action_lock"):
            await self._action_lock.acquire()
        try:
            profile.decide_generation(self._generation)
            if binding.mode is ActionMode.EXCLUSIVE and generation not in {None, self._generation}:
                await self._acknowledge(interaction, profile=profile, source="stale")
                profile.presentation = PresentationStatus.ACKNOWLEDGED
                profile.finish(DispatchDisposition.STALE)
                return
            rebased = binding.mode is ActionMode.REBASE and generation not in {None, self._generation}
            profile.decide_generation(self._generation, rebased=rebased)
            if binding.mode is ActionMode.REBASE and rebase is not None:
                # Resolved inside the lock: outside it, "newest" is whatever happened to be
                # committed before this action started waiting for its turn.
                with profile.operation.span("generation"):
                    refreshed = rebase()
                if refreshed is None:
                    await self._acknowledge(interaction, profile=profile, source="stale")
                    profile.presentation = PresentationStatus.ACKNOWLEDGED
                    profile.finish(DispatchDisposition.STALE)
                    return
                binding = refreshed
            # Inside the lock, so a `when(...)` closure reads component state nobody is
            # writing, and before the transaction, so a denial writes nothing.
            admission = await self._admit(binding, key, interaction, values, profile)
            if admission.challenge is None:
                if not admission.admitted:
                    return
                await invoke(binding, rebased, self._generation)
                return
            challenged = admission.challenge
        finally:
            self._action_lock.release()
        # Only a challenge falls out of the block above rather than returning from inside it.
        # The dialog is opened with the lock released and the press already over, so every
        # other control on the panel stays live while the actor reads the question.
        await self._present_challenge(challenged, key, interaction, values, profile)

    async def _invoke(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: _SelectionValues,
        submitted_generation: int | None,
        active_generation: int,
        rebased: bool,
        profile: _DispatchProfile,
    ) -> None:
        # Painting busy may wait behind arbitrary visible-resource work under the render
        # lock. The acknowledgement deadline is independent so only Discord can delay it.
        busy = (
            None if binding.busy is None else _BusyPaint(self, key, binding.busy, interaction, resumed=profile.resumed)
        )

        async def acknowledge_by_deadline() -> None:
            await anyio.sleep(self.acknowledgement_timeout)
            deferred = await self._acknowledge(interaction, profile=profile, source="watchdog")
            if deferred:
                profile.operation.mark_deadline_missed()

        async def paint_when_slow() -> None:
            await anyio.sleep(min(self.pending_after, self.acknowledgement_timeout))
            await busy.show(profile)  # type: ignore[union-attr]

        # A handler is the other place loads start -- `Resource.reload`, or a pattern's
        # `refresh` -- and two fast presses supersede each other the same way a settle pass does.
        with abandon_superseded_loads(anyio.CancelScope), _unwrapped():
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(acknowledge_by_deadline)
                if busy is not None:
                    tasks.start_soon(paint_when_slow)
                await self._invoke_and_flush(
                    binding,
                    key,
                    interaction,
                    values,
                    submitted_generation,
                    active_generation,
                    rebased,
                    profile,
                    busy,
                )
                tasks.cancel_scope.cancel()

    async def _invoke_and_flush(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: _SelectionValues,
        submitted_generation: int | None,
        active_generation: int,
        rebased: bool,
        profile: _DispatchProfile,
        busy: _BusyPaint | None = None,
    ) -> None:
        event = self._event(interaction, values)
        action_context = ActionContext.create(
            f"{type(self.component).__name__}.{key}",
            actor=ActorRef("discord_user", str(interaction.user.id)),
            metadata={"message_root_id": self.id, "generation": str(active_generation)},
        )
        request = ActionRequest(
            event,
            key,
            InteractionKind.PRESS if values is None else InteractionKind.SELECTION,
            binding.mode,
            submitted_generation,
            active_generation,
            action_context,
            rebased,
        )

        async def handle() -> None:
            with self._action_transaction(binding.mode, action_context):
                # Before the handler: the entry is the transaction's whole delta either
                # way, and reserving the history here is what makes a handler's own
                # `record` the error it is.
                if binding.record is not None:
                    binding.record.record(binding.label)
                await binding.handler(event)

        try:
            handled = await self._run_middleware(request, handle, profile.operation)
        except Exception as error:
            profile.action = ActionStatus.FAILED
            # Before the error hook: the failed action leaves no flush behind, so without
            # this the panel would sit on "working" with every control dead.
            restore = binding.busy is not None and binding.busy.restore_on_error
            if busy is not None and await busy.close() and restore:
                await busy.restore()
            await self.handle_error(interaction, error, f"action:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACTION_FAILED, error)
            return
        profile.action = ActionStatus.HANDLED if handled else ActionStatus.SHORT_CIRCUITED
        profile.acknowledge("action")
        self._renew(interaction, resumed=profile.resumed)
        painted = busy is not None and await busy.close()
        try:
            profile.presentation = await self.refresh(interaction, profile=profile)
        except Exception as error:
            profile.presentation = PresentationStatus.FAILED
            profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
            raise
        # An action that changed nothing flushes nothing, and a stranded "working" panel is
        # not a policy choice -- so this restore ignores `restore_on_error`.
        if painted and busy is not None and profile.presentation is not PresentationStatus.WRITTEN:
            await busy.restore()
        profile.finish(DispatchDisposition.COMPLETED)

    async def _invoke_submit(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        spec: FormSpec,
        values: Mapping[str, object],
        generation: int | None,
        active_generation: int,
        rebased: bool,
        profile: _DispatchProfile,
    ) -> None:
        async def watchdog() -> None:
            await anyio.sleep(self.acknowledgement_timeout)
            deferred = await self._acknowledge(interaction, profile=profile, source="watchdog")
            if deferred:
                profile.operation.mark_deadline_missed()

        with abandon_superseded_loads(anyio.CancelScope), _unwrapped():
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(watchdog)
                await self._invoke_submit_and_flush(
                    binding,
                    key,
                    interaction,
                    spec,
                    values,
                    generation,
                    active_generation,
                    rebased,
                    profile,
                )
                tasks.cancel_scope.cancel()

    async def _invoke_submit_and_flush(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        spec: FormSpec,
        values: Mapping[str, object],
        generation: int | None,
        active_generation: int,
        rebased: bool,
        profile: _DispatchProfile,
    ) -> None:
        try:
            with profile.operation.span("form_evaluation"):
                evaluation = await spec.evaluate(values)
            actor = Actor(str(interaction.user.id), getattr(interaction.user, "display_name", None))
            responder = ActionResponder(interaction, self)
            event = SubmitEvent(
                actor,
                responder,
                self.localization.locale,
                {"frontend": "discord"},
                evaluation.values,
                evaluation.attempted,
                evaluation.errors,
            )
            if evaluation.errors and spec.validation is FormValidationMode.RETRY:
                await responder.retry_form(
                    spec.with_prefill(evaluation.attempted),
                    evaluation.errors,
                    key=key,
                    handler=binding.handler,
                    mode=binding.mode,
                    generation=self._generation if generation is None else generation,
                    actor_id=interaction.user.id,
                    label=binding.label,
                    record=binding.record,
                )
                profile.presentation = PresentationStatus.WRITTEN
                profile.acknowledge("validation_retry")
                profile.finish(DispatchDisposition.VALIDATION_RETRY)
                return
            request = ActionRequest(
                event,
                key,
                InteractionKind.SUBMIT,
                binding.mode,
                generation,
                active_generation,
                action_context := ActionContext.create(
                    f"{type(self.component).__name__}.{key}",
                    actor=ActorRef("discord_user", str(interaction.user.id)),
                    metadata={"message_root_id": self.id, "generation": str(active_generation)},
                ),
                rebased,
            )

            async def handle() -> None:
                with self._action_transaction(binding.mode, action_context):
                    if binding.record is not None:
                        binding.record.record(binding.label)
                    await binding.handler(event)

            handled = await self._run_middleware(request, handle, profile.operation)
            profile.action = ActionStatus.HANDLED if handled else ActionStatus.SHORT_CIRCUITED
        except Exception as error:
            profile.action = ActionStatus.FAILED
            await self.handle_error(interaction, error, f"form:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACTION_FAILED, error)
            return
        profile.acknowledge("action")
        self._renew(interaction)
        try:
            profile.presentation = await self.refresh(interaction, profile=profile)
        except Exception as error:
            profile.presentation = PresentationStatus.FAILED
            profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
            raise
        profile.finish(DispatchDisposition.COMPLETED)

    async def _run_middleware(
        self,
        request: ActionRequest,
        endpoint: ActionProceed,
        operation: OperationRecorder,
    ) -> bool:
        """Compose the frozen mount middleware in first-listed, outermost order."""

        handled = False
        action_attributes = {"action_id": str(request.context.action_id)}

        async def invoke(index: int) -> None:
            nonlocal handled
            if index == len(self._middleware):
                handled = True
                with operation.span("handler", attributes=action_attributes):
                    await endpoint()
                return

            active = True
            called = False

            async def proceed() -> None:
                nonlocal called
                if not active:
                    message = "action middleware proceed() is only valid during dispatch()"
                    raise RuntimeError(message)
                if called:
                    message = "action middleware proceed() may only be called once"
                    raise RuntimeError(message)
                called = True
                await invoke(index + 1)

            try:
                middleware = self._middleware[index]
                provenance = f"{type(middleware).__module__}.{type(middleware).__qualname__}"
                with operation.span(f"middleware:{provenance}", attributes=action_attributes):
                    await middleware.dispatch(request, proceed)
            finally:
                active = False

        await invoke(0)
        return handled

    async def _acknowledge(
        self,
        interaction: discord.Interaction,
        *,
        profile: _DispatchProfile | None = None,
        source: str = "framework",
    ) -> bool:
        deferred = False
        if not interaction.response.is_done():
            await interaction.response.defer()
            deferred = True
        if profile is not None:
            profile.acknowledge(source if deferred else "action")
        return deferred

    async def _refresh_through(
        self,
        interaction: discord.Interaction,
        *,
        profile: _DispatchProfile | None = None,
    ) -> PresentationStatus:
        """Apply pending state changes as an interaction edit, or just acknowledge."""
        if profile is not None:
            with profile.operation.span("flush"):
                return await self._flush(interaction, profile.operation, dispatch=profile)
        with self.profiler.operation(
            OperationKind.DELIVERY, name="flush", attributes={"message_root_id": self.id}
        ) as operation:
            try:
                presentation = await self._flush(interaction, operation)
            except Exception:
                operation.set_result(TraceResult(TraceStatus.FAILED, presentation=PresentationStatus.FAILED))
                raise
            status = TraceStatus.ABANDONED if presentation is PresentationStatus.ABANDONED else TraceStatus.COMPLETED
            operation.set_result(TraceResult(status, presentation=presentation))
            return presentation

    async def _flush(
        self,
        interaction: discord.Interaction,
        operation: OperationRecorder,
        *,
        dispatch: _DispatchProfile | None = None,
    ) -> PresentationStatus:
        acknowledge = False
        presentation = PresentationStatus.NO_CHANGE
        with operation.span("render_lock"):
            await self._render_lock.acquire()
        try:
            if self._finished:
                return PresentationStatus.ABANDONED
            if self._lifecycle is MessageRootStatus.RENEWAL_ARMED:
                acknowledge = True
            if not self._dirty:
                acknowledge = True
            elif self._lifecycle is MessageRootStatus.ACTIVE:
                # A component cannot enter the tree without a state write, so a click that
                # changed nothing never reaches this at all.
                candidate = await self._stage_loaded(profile=operation, preflight=True)
                source = self._source(interaction, resumed=dispatch is not None and dispatch.resumed)
                if self._same_as_live(candidate):
                    with operation.span("suppress"):
                        self._suppress(candidate, operation)
                    acknowledge = True
                    presentation = PresentationStatus.UNCHANGED
                    # Only visible resources could still move the panel; they settle through
                    # their own comparison.
                    await self._settle_visible(candidate, through=source, profile=operation)
                else:
                    candidate = _drawn(candidate)
                    try:
                        wrote = await self._deliver(candidate, through=source, profile=operation)
                    except Exception:
                        with operation.span("rollback"):
                            self._rollback(candidate)
                        raise
                    if wrote is None:
                        with operation.span("rollback"):
                            self._rollback(candidate)
                        acknowledge = True
                        presentation = PresentationStatus.ABANDONED
                    else:
                        with operation.span("commit"):
                            self._commit_presented(candidate)
                        await self._settle_visible(candidate, through=source, profile=operation)
                        presentation = PresentationStatus.WRITTEN
                        if dispatch is not None and wrote is source:
                            dispatch.acknowledge("interaction_write")
                        # Only the interaction's own handle answers the click by editing through
                        # it. Delivery through the standing handle leaves the click unanswered,
                        # and Discord shows the user "This interaction failed" three seconds later.
                        acknowledge = wrote is not source
        finally:
            self._render_lock.release()
        if acknowledge:
            await self._acknowledge(interaction, profile=dispatch, source="flush")
            if presentation is PresentationStatus.NO_CHANGE:
                presentation = PresentationStatus.ACKNOWLEDGED
        return presentation

    async def finish_via(self, interaction: discord.Interaction) -> None:
        """End this message root through an interaction edit, as a Close button needs."""
        run_hooks = False
        try:
            async with self._render_lock:
                if self._finished:
                    return
                # Marked before delivery: a failed disable-edit must not resurrect the mount.
                self._finished = True
                candidate = (
                    self._draw_renewal(disabled=True)
                    if self._lifecycle is MessageRootStatus.RENEWAL_ARMED
                    else self._stage(disabled=True)
                )
                source = deliver.handle_from(interaction)
                try:
                    wrote = await self._deliver(candidate, through=source, files=False)
                    # Editing through the interaction's own handle answers the click; nothing else
                    # does, whether it delivered through the standing handle or not at all.
                    if wrote is None or wrote is not source:
                        await self._acknowledge(interaction)
                except Exception:
                    if isinstance(candidate, _Candidate):
                        self._rollback(candidate)
                    raise
                finally:
                    # The terminal tree is never committed, so `finish` unmounts the live one once.
                    candidate.view.stop()
                    self._teardown()
                    # In the `finally` rather than after it: a raising disable-edit propagates past
                    # this block, and the mount is finished and torn down either way. An observer
                    # that missed it would hold a dead mount forever.
                    run_hooks = True
        except BaseException:
            if run_hooks:
                await self._hooks.run_finish(self)
            raise
        if run_hooks:
            await self._hooks.run_finish(self)

    async def schedule(self) -> None:
        """Ask for an out-of-band re-render (background state change, not an interaction).

        Shows the newest state at the next opportunity rather than this instant: a scheduler
        coalesces requests, and a mount with no live `handle` — an ephemeral message nobody
        has clicked lately — keeps the render in `pending` until someone clicks it again.
        """
        if self.scheduler is not None:
            self.scheduler.schedule(self)
            return
        await self.refresh()

    async def refresh(
        self,
        interaction: discord.Interaction | None = None,
        *,
        links: Sequence[TraceLink] = (),
        profile: _DispatchProfile | None = None,
    ) -> PresentationStatus:
        """Re-render and deliver right now, reporting how the presentation settled.

        With an `interaction`, the delivery is that interaction's edit; without one it is an
        out-of-band render against the mount's own edit authority. One verb rather than the
        three this used to have, because both do the same thing to the same subject.
        """
        if interaction is not None:
            return await self._refresh_through(interaction, profile=profile)
        return await self._refresh_now(links=links, reuse_committed=_SCHEDULED_REFRESH.get() is self)

    @contextmanager
    def _scheduled_delivery(self) -> Iterator[None]:
        token = _SCHEDULED_REFRESH.set(self)
        try:
            yield
        finally:
            _SCHEDULED_REFRESH.reset(token)

    async def _refresh_now(
        self,
        *,
        links: Sequence[TraceLink] = (),
        reuse_committed: bool = False,
    ) -> PresentationStatus:
        component = type(self.component)
        name = f"{component.__module__}.{component.__qualname__}"
        with self.profiler.operation(
            OperationKind.REFRESH, name=name, attributes={"message_root_id": self.id}, links=links
        ) as profile:
            with profile.span("render_lock"):
                await self._render_lock.acquire()
            try:
                if self._finished:
                    profile.set_result(TraceResult(TraceStatus.ABANDONED, presentation=PresentationStatus.ABANDONED))
                    return PresentationStatus.ABANDONED
                armed = await self._apply_expiry_arm(profile)
                if armed is not None:
                    status = TraceStatus.ABANDONED if armed is PresentationStatus.ABANDONED else TraceStatus.COMPLETED
                    profile.set_result(TraceResult(status, presentation=armed))
                    return armed
                if self._lifecycle is MessageRootStatus.RENEWAL_ARMED:
                    profile.set_result(TraceResult(TraceStatus.COMPLETED, presentation=PresentationStatus.NO_CHANGE))
                    return PresentationStatus.NO_CHANGE
                if self._handle is None or self._handle.expired():
                    self._dirty = True
                    profile.set_result(TraceResult(TraceStatus.ABANDONED, presentation=PresentationStatus.ABANDONED))
                    return PresentationStatus.ABANDONED
                candidate = await self._stage_loaded(
                    profile=profile,
                    preflight=True,
                    reuse_committed=reuse_committed,
                )
                if self._same_as_live(candidate):
                    with profile.span("suppress"):
                        self._suppress(candidate, profile)
                    await self._settle_visible(candidate, profile=profile)
                    profile.set_result(TraceResult(TraceStatus.COMPLETED, presentation=PresentationStatus.UNCHANGED))
                    return PresentationStatus.UNCHANGED
                candidate = _drawn(candidate)
                try:
                    delivered = await self._deliver(candidate, profile=profile) is not None
                except Exception:
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    profile.set_result(TraceResult(TraceStatus.FAILED, presentation=PresentationStatus.FAILED))
                    raise
                if not delivered:
                    # `_rollback` leaves the mount dirty, so the next interaction shows this render.
                    # `refresh` has always promised the next opportunity rather than this instant.
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    logger.debug("mount %s has no live edit handle; render deferred", self.id)
                    profile.set_result(TraceResult(TraceStatus.ABANDONED, presentation=PresentationStatus.ABANDONED))
                    return PresentationStatus.ABANDONED
                with profile.span("commit"):
                    self._commit_presented(candidate)
                await self._settle_visible(candidate, profile=profile)
                profile.set_result(TraceResult(TraceStatus.COMPLETED, presentation=PresentationStatus.WRITTEN))
                return PresentationStatus.WRITTEN
            finally:
                self._render_lock.release()

    def on_presented(self, callback: PresentedHook) -> None:
        """Synchronously observe future generations after delivery and commit succeed.

        The callback runs under the render lock and must not await or call an operation that
        acquires it. Schedule asynchronous follow-up through an owned supervisor or a queue.
        """
        self._hooks.presented.append(callback)

    def on_committed(self, callback: CommittedHook) -> None:
        """Synchronously observe application commits, including suppressed presentations.

        The callback runs under the render lock and must not await or call an operation that
        acquires it. Schedule asynchronous follow-up through an owned supervisor or a queue.
        """
        self._hooks.committed.append(callback)

    def on_finish(self, callback: FinishHook) -> None:
        """Call `callback` once this mount has finished, after its teardown.

        Fires from every terminal path -- `finish`, `finish_via`, and the timeout that
        delegates to `finish` -- including one whose disable-edit failed. Callbacks run in
        registration order, and an exception is logged and swallowed: a broken observer must
        not abort another's cleanup, nor teardown itself.

        Calling `finish` from inside a hook is a no-op, so an observer that cascades to other
        message roots cannot loop back into this one. A hook registered on an already-finished message root
        never fires -- `finished` is the caller's to check first.
        """
        self._hooks.finish.append(callback)

    async def finish(self, *, disable: bool = True, retain_routed: bool = False) -> None:
        """End this message root and stop dispatch, optionally retaining stateless routes."""
        run_hooks = False
        try:
            async with self._render_lock:
                if self._finished:
                    return
                self._finished = True
                try:
                    if disable and self._handle is not None:
                        candidate = (
                            self._draw_renewal(disabled=True)
                            if self._lifecycle is MessageRootStatus.RENEWAL_ARMED
                            else self._stage(disabled=True)
                        )
                        if retain_routed:
                            children = (
                                candidate.view.walk_children()
                                if isinstance(candidate.view, discord.ui.LayoutView)
                                else candidate.view.children
                            )
                            for item in children:
                                target = item.item if isinstance(item, discord.ui.DynamicItem) else item
                                if isinstance(target, RoutedItem | RoutedSelectItem):
                                    target.disabled = False
                        try:
                            if await self._deliver(candidate, files=False) is None:
                                logger.debug("could not disable controls on finish: no live edit handle")
                                if isinstance(candidate, _Candidate):
                                    self._rollback(candidate)
                        except discord.HTTPException:
                            logger.debug("could not disable controls on finish", exc_info=True)
                            if isinstance(candidate, _Candidate):
                                self._rollback(candidate)
                        finally:
                            candidate.view.stop()
                finally:
                    # Neither the teardown nor the hooks are conditional on the disable-edit
                    # working, or even on it failing in a way this anticipated. The mount is
                    # finished either way, and an observer that never heard so would hold a dead
                    # mount forever.
                    self._teardown()
                    run_hooks = True
        except BaseException:
            if run_hooks:
                await self._hooks.run_finish(self)
            raise
        if run_hooks:
            await self._hooks.run_finish(self)

    async def dismiss(self) -> None:
        """Delete the delivered message and finish this mount."""
        run_hooks = False
        try:
            async with self._render_lock:
                if self._finished:
                    return
                self._finished = True
                try:
                    if self._delete_handle is not None:
                        await self._delete_handle.delete()
                finally:
                    self._teardown()
                    run_hooks = True
        except BaseException:
            if run_hooks:
                await self._hooks.run_finish(self)
            raise
        if run_hooks:
            await self._hooks.run_finish(self)

    def _teardown(self) -> None:
        """Stop the live view and unmount the committed tree, once."""
        if self._unwatch_expiry is not None:
            self._unwatch_expiry()
            self._unwatch_expiry = None
        if self._view is not None:
            self._view.stop()
            self._view = None
        self._subscriptions.close()
        self._expiry_arm_requested = None
        self.runtime.finish()
        if self._owns_render_cache:
            self.render_cache.clear()

    async def handle_timeout(self) -> None:
        await self.finish(disable=True, retain_routed=self.retain_routed_on_timeout)

    async def handle_error(self, interaction: discord.Interaction, error: Exception, source: str) -> None:
        if self.on_error is not None:
            await self.on_error(interaction, error, source)
            return
        logger.error("unhandled component error in %s", source, exc_info=error)


def owner_message_root(
    component: Component[ComponentsV2Target],
    user_id: int,
    **options: Unpack[MessageRootBehaviorOptions],
) -> MessageRoot:
    """Construct a V2 mount whose controls belong to one Discord user."""
    return MessageRoot(component, access=Owner(user_id), **options)

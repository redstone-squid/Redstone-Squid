"""The mount: one component bound to one Discord message.

Every interaction funnels through :meth:`Mount.dispatch` — access policy, handler, error hook,
and the re-render/edit cycle live here once instead of per view. The mount outlives its
discord.py views: each render produces a fresh :class:`MountedView`, and the previous one is
stopped after a successful edit so dispatch tables do not accumulate.
"""

import asyncio
import hashlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast, override

import anyio
import discord

from squid_layouts.actions import (
    ActionBinding,
    ActionEvent,
    ActionKind,
    ActionMiddleware,
    ActionPolicy,
    ActionProceed,
    ActionRequest,
    Actor,
    Feedback,
    PressEvent,
    SelectionEvent,
    SubmitEvent,
)
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME, LOCALIZATION_CONTEXT, Chrome, localize_chrome
from squid_layouts.discord import delivery as deliver
from squid_layouts.discord import live
from squid_layouts.discord.access import AccessPolicy, Allowed, Denied, Owner
from squid_layouts.discord.actions import ActionResponder
from squid_layouts.discord.attachments import files_for
from squid_layouts.discord.compose import Composition, compose
from squid_layouts.discord.presentation import DiscordPresentation
from squid_layouts.discord.renderer import V2Renderer
from squid_layouts.discord.target import V2_TARGET, Target
from squid_layouts.document import Asset, Document
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import FormBinding, FormSpec, FormValidationPolicy, SubmitHandler
from squid_layouts.guards import GuardLedger
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.limits import LIMITS, DiscordLimits
from squid_layouts.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    NavFactory,
    NavigationContext,
    NavigationState,
    default_nav,
)
from squid_layouts.primitives.nodes import Node
from squid_layouts.profiling import (
    ActionOutcome,
    DetachedSpanRecorder,
    DispatchDisposition,
    DispatchResult,
    GenerationDecision,
    NoOpProfiler,
    OperationKind,
    OperationRecorder,
    PresentationOutcome,
    Profiler,
    TraceLink,
    TraceOutcome,
    TraceResult,
)

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.runtime.component import Component, ComponentTree
from squid_layouts.runtime.owner import ComponentRuntime
from squid_layouts.runtime.presentation import PresentationSession, SessionUpdate, apply_updates
from squid_layouts.runtime.reactivity import readonly_transaction, transaction
from squid_layouts.runtime.resources import Resource, ResourceDelivery
from squid_layouts.scene.model import (
    PlanMetrics,
    PlanReport,
    PlanResult,
    SceneButton,
    SceneDocument,
    SceneSelect,
)
from squid_layouts.semantic import Status
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization, TextLike, resolve_text

logger = logging.getLogger(__name__)
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


def _needs_load(component: Component) -> bool:
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


class ErrorHook(Protocol):
    """Host-provided handler for exceptions escaping a component callback."""

    def __call__(self, interaction: discord.Interaction, error: Exception, source: str) -> Awaitable[None]: ...


class FinishHook(Protocol):
    """Observer told that a mount has finished, after its teardown."""

    # Positional-only, as `Destination` is: a named parameter would make the protocol demand
    # that every observer spell the argument `mount`.
    def __call__(self, mount: Mount, /) -> Awaitable[None]: ...


class PresentedHook(Protocol):
    """Observer told that Discord accepted and the mount committed a generation."""

    def __call__(self, mount: Mount, /) -> Awaitable[None]: ...


class Scheduler(Protocol):
    """Anything that can absorb out-of-band refresh requests (see `Reactor`)."""

    def schedule(self, mount: Mount) -> None: ...


def _unique_by_identity(middleware: Sequence[ActionMiddleware]) -> tuple[ActionMiddleware, ...]:
    """Freeze middleware while treating the same installed instance as idempotent."""
    unique: list[ActionMiddleware] = []
    for candidate in middleware:
        if not any(existing is candidate for existing in unique):
            unique.append(candidate)
    return tuple(unique)


class MountedView(discord.ui.LayoutView):
    """One render generation of a mounted component."""

    def __init__(self, mount: Mount, timeout: float | None) -> None:
        super().__init__(timeout=timeout)
        self._mount = mount

    async def on_timeout(self) -> None:
        await self._mount.handle_timeout()

    @override
    def is_dispatchable(self) -> bool:
        # A mount wants storing even when it draws nothing dispatchable, because
        # `store_view` is gated on this and `add_view` is what starts the timeout task.
        # A document of nothing but routed controls would otherwise never time out.
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await self._mount.handle_error(interaction, error, f"item:{type(item).__name__}")


def _custom_id(mount_id: str, generation: int, key: str) -> str:
    """A per-render-unique control id for ``key``, within Discord's 100-char limit.

    Nested components produce long dotted keys, and truncating those makes two controls
    collide — Discord rejects the message and, worse, a click could route to the wrong
    handler. Digest the key instead; dispatch itself goes by the in-process key.

    The generation is part of the id because discord.py registers a replacement view before
    the mount stops its predecessor. Reusing ids lets the predecessor unregister the new
    view's controls when it stops, leaving visible buttons with no callback.
    """
    prefix = f"ctl:{mount_id}:{generation}:"
    custom_id = f"{prefix}{key}"
    if len(custom_id) <= 100:
        return custom_id
    return f"{prefix}#{hashlib.blake2s(key.encode()).hexdigest()[:12]}"


class _WiredButton(discord.ui.Button[MountedView]):
    def __init__(self, node: SceneButton, mount: Mount, key: str, generation: int) -> None:
        super().__init__(
            style=getattr(discord.ButtonStyle, node.style.value),
            label=node.label,
            emoji=node.emoji,
            disabled=node.disabled,
            custom_id=_custom_id(mount.id, generation, key),
        )
        self._mount = mount
        self._key = key
        self._generation = generation

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._mount.dispatch(self._key, interaction, generation=self._generation)


class _WiredSelect(discord.ui.Select[MountedView]):
    def __init__(self, node: SceneSelect, mount: Mount, key: str, generation: int) -> None:
        super().__init__(
            placeholder=node.placeholder,
            min_values=node.min_values,
            max_values=node.max_values,
            disabled=node.disabled,
            custom_id=_custom_id(mount.id, generation, key),
            options=[
                discord.SelectOption(
                    label=option.label, value=option.value, description=option.description, default=option.default
                )
                for option in node.options
            ],
        )
        self._mount = mount
        self._key = key
        self._generation = generation

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._mount.dispatch(self._key, interaction, self.values, generation=self._generation)


@dataclass(frozen=True, slots=True)
class _SubmitBinding(ActionBinding):
    """A form submission's binding, carrying the schema its values must be parsed against."""

    spec: FormSpec | None = None


@dataclass(slots=True)
class _Candidate:
    """One staged render generation, which becomes the mount's state only when committed."""

    view: MountedView
    composition: Composition
    tree: ComponentTree
    handlers: dict[str, ActionBinding]
    form_bindings: Mapping[str, FormBinding]
    generation: int
    revision: int
    assets: tuple[Asset, ...]
    # Presentation writes this render earned; a failed delivery simply drops them.
    session_updates: tuple[SessionUpdate, ...]

    @property
    def presentation(self) -> DiscordPresentation:
        """The complete message this render delivers to."""
        return DiscordPresentation.components_v2(self.view, assets=self.assets)


@dataclass(frozen=True, slots=True)
class MountAddress:
    """Where a mount's message is -- for links and diagnostics, never for writing to it.

    Writing goes through an :class:`~squid_layouts.discord.delivery.EditHandle`, which is
    about credentials and when they expire. These are only coordinates, so they stay true
    after every handle to the message has gone stale.
    """

    message_id: int
    channel_id: int
    guild_id: int | None
    jump_url: str
    ephemeral: bool


@dataclass(frozen=True, slots=True)
class MountSnapshot:
    """One read-only look at a live mount, for host diagnostics.

    A single call rather than a dozen properties: it fixes what a mount is willing to say
    about itself, and a caller cannot accidentally mutate what it reads. Everything here is
    either a scalar or already immutable, so nothing is copied. The deeper payloads — the
    components' declared state and the presentation session — stay behind `runtime` and
    `presentation`, because building them costs more than a list of sessions should.
    """

    id: str
    component: str
    """Qualified class name of the root component."""
    address: MountAddress | None
    generation: int
    pending: bool
    finished: bool
    age: float
    """Seconds since the mount was constructed."""
    idle: float
    """Seconds since the initial send or last accepted click — what the timeout counts."""
    expires_in: float | None
    """Seconds of idle timeout left, or `None` for a mount that never times out."""
    access: AccessPolicy
    handler_keys: tuple[str, ...]
    """Action keys the live generation answers to."""
    scene: SceneDocument | None
    report: PlanReport | None
    metrics: PlanMetrics | None


@dataclass(slots=True)
class _DispatchProfile:
    """Mutable operation-local facts frozen into a dispatch result at the terminal branch."""

    operation: OperationRecorder
    interaction: discord.Interaction
    generation: GenerationDecision
    acknowledgement: DetachedSpanRecorder
    action: ActionOutcome = ActionOutcome.NOT_RUN
    presentation: PresentationOutcome = PresentationOutcome.NOT_REQUIRED
    finished: bool = False

    def decide_generation(self, active: int, *, rebased: bool = False) -> None:
        self.generation = GenerationDecision(self.generation.submitted, active, rebased)

    def acknowledge(self, source: str) -> None:
        if self.interaction.response.is_done():
            self.acknowledgement.set_attribute("source", source)
            self.acknowledgement.finish()

    def finish(self, disposition: DispatchDisposition, error: Exception | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        self.operation.increment("dispatch.rebased", int(self.generation.rebased))
        self.acknowledge("action")
        outcome = (
            TraceOutcome.CANCELLED
            if disposition is DispatchDisposition.CANCELLED
            else TraceOutcome.FAILED
            if disposition
            in {
                DispatchDisposition.ACCESS_FAILED,
                DispatchDisposition.GUARD_FAILED,
                DispatchDisposition.ACTION_FAILED,
                DispatchDisposition.DELIVERY_FAILED,
            }
            else TraceOutcome.COMPLETED
        )
        detail = None if error is None else f"{type(error).__module__}.{type(error).__qualname__}"
        self.operation.set_result(
            TraceResult(
                outcome,
                detail,
                DispatchResult(disposition, self.action, self.presentation, self.generation),
            )
        )


class _BusyPaint:
    """One action's interim "working" render, and the ordering between it and the flush.

    The paint is scheduled by the acknowledgement watchdog and the flush by the handler
    returning, so the two race. They are ordered by this object's lock rather than by
    cancellation: `close()` waits out a paint already in flight and then latches, so a
    watchdog that wakes late paints nothing over the final render.
    """

    def __init__(self, mount: Mount, key: str, feedback: Feedback, interaction: discord.Interaction) -> None:
        self._mount = mount
        self._key = key
        self._feedback = feedback
        self._interaction = interaction
        self._lock = asyncio.Lock()
        self._closed = False
        self._shown = False

    @property
    def shown(self) -> bool:
        """Whether an interim render is currently what the reader is looking at."""
        return self._shown

    async def show(self, profile: _DispatchProfile) -> None:
        """Relabel the pressed control and disable the panel, once."""
        async with self._lock:
            if self._closed or self._shown or self._mount._finished:
                return
            pending = self._feedback.pending
            label = self._mount._chrome_text(self._mount.chrome.working if pending is None else pending)
            source = deliver.handle_from(self._interaction)
            wrote = await self._mount._repaint(self._key, label, through=source)
            if wrote is None:
                # Nothing could write, so the click is still unanswered: the watchdog goes
                # on to defer it at the usual deadline.
                return
            self._shown = True
            if wrote is source:
                profile.acknowledge("busy")

    async def close(self) -> bool:
        """Stop any further painting and report whether one is on screen."""
        async with self._lock:
            self._closed = True
            return self._shown

    async def restore(self) -> None:
        """Put the committed scene back, live controls and all."""
        async with self._lock:
            if not self._shown or self._mount._finished:
                return
            self._shown = False
            await self._mount._repaint(None, None, through=deliver.handle_from(self._interaction))


class Mount:
    """Binds a component to a message and owns its whole interaction lifecycle."""

    def __init__(
        self,
        component: Component,
        *,
        access: AccessPolicy,
        target: Target = V2_TARGET,
        chrome: Chrome = DEFAULT_CHROME,
        localization: Localization = NEUTRAL,
        palette: Palette = DEFAULT_PALETTE,
        strict: bool = False,
        timeout: float | None = 900,
        on_error: ErrorHook | None = None,
        middleware: Sequence[ActionMiddleware] = (),
        profiler: Profiler | None = None,
        scheduler: Scheduler | None = None,
        nav: NavFactory | None = None,
        acknowledgement_timeout: float = 2.5,
        pending_after: float = 1.0,
        clock: Callable[[], float] = _monotonic,
    ) -> None:
        self.id = secrets.token_urlsafe(6)
        self.component = component
        self.clock = clock
        # Diagnostics only. `_active` is what the idle timeout counts from: the initial send
        # and each accepted click move it, while unattended refreshes deliberately do not.
        self._born = self._active = self.clock()
        self.address: MountAddress | None = None
        """Where this mount's message is, once it has one. Read `handle` to write to it."""
        self._chrome = chrome
        self.localization = localization
        self.palette = palette
        self.chrome = localize_chrome(chrome, localization)
        self.nav = nav if nav is not None else default_nav
        self.runtime = ComponentRuntime(
            component,
            on_invalidate=self._mark_dirty,
            context={
                CHROME_CONTEXT: self.chrome,
                LOCALIZATION_CONTEXT: localization,
                NAV_FACTORY_CONTEXT: self.nav,
            },
        )
        self.acknowledgement_timeout = acknowledgement_timeout
        self.pending_after = pending_after
        """How long an action carrying `Feedback` may run before its interim paint appears."""
        self.guards = GuardLedger(now=clock)
        """Where stateful guards keep their counts; it lives and dies with this mount."""
        self.target = target
        """The message mode this mount owns for its whole life.

        A mount has one target: changing it means opening a replacement mount, not swapping
        a live mount's renderer out from under its action bindings.
        """
        self.limits = target.limits if isinstance(target.limits, DiscordLimits) else LIMITS
        self.strict = strict
        self.timeout = timeout
        self.access = access
        self.on_error = on_error
        self._middleware = _unique_by_identity(middleware)
        inherited_profiler = None if scheduler is None else getattr(scheduler, "profiler", None)
        self.profiler = (
            profiler
            if profiler is not None
            else cast(Profiler, inherited_profiler)
            if inherited_profiler is not None
            else _NOOP_PROFILER
        )
        self.scheduler = scheduler
        self.status: TextLike | None = None
        """Framework-drawn status appended to the document until the next accepted interaction."""
        self._handle: deliver.EditHandle | None = None
        self._view: MountedView | None = None
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
        self._pending: _Candidate | None = None
        self._dirty = False
        self._finished = False
        self._finish_hooks: list[FinishHook] = []
        self._presented_hooks: list[PresentedHook] = []
        self._hooks_fired = False
        self._assets: tuple[Asset, ...] = ()
        self._plan: PlanResult | None = None

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

    @property
    def pending(self) -> bool:
        """Whether a render is staged that Discord has not seen."""
        return self._dirty

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

    def snapshot(self) -> MountSnapshot:
        """Describe this mount for a diagnostics surface. See :class:`MountSnapshot`."""
        now = self.clock()
        idle = now - self._active
        component = type(self.component)
        return MountSnapshot(
            id=self.id,
            component=f"{component.__module__}.{component.__qualname__}",
            address=self.address,
            generation=self._generation,
            pending=self._dirty,
            finished=self._finished,
            age=now - self._born,
            idle=idle,
            expires_in=None if self.timeout is None else max(0.0, self.timeout - idle),
            access=self.access,
            handler_keys=tuple(sorted(self._handlers)),
            scene=None if self._plan is None else self._plan.scene,
            report=None if self._plan is None else self._plan.report,
            metrics=None if self._plan is None else self._plan.metrics,
        )

    def _remaining_timeout(self) -> float | None:
        if self.timeout is None:
            return None
        return max(0.0, self.timeout - (self.clock() - self._active))

    def _note_address(self, message: discord.Message | None) -> None:
        """Remember where this mount's message is, the first time Discord says.

        Coordinates never change for a given mount, so this is set once and kept. It reads
        only what a `Message` already carries, and a `None` from an interaction that has no
        message simply leaves the mount unlocated.
        """
        if message is None or self.address is not None:
            return
        self.address = MountAddress(
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=None if message.guild is None else message.guild.id,
            jump_url=message.jump_url,
            ephemeral=bool(message.flags.ephemeral),
        )

    # --- Rendering ---------------------------------------------------------------------

    def _stage_view(self, *, disabled: bool = False) -> MountedView:
        """Stage a render of the component's current state into a fresh view, committing none of it.

        Private, because a staged generation is not the mount's state: nothing here moves
        handlers, lifecycle hooks, page positions or the live generation, so handing one to a
        send path shows Discord a generation the mount does not own. Delivery goes through
        :meth:`send` or :meth:`flush`, which stage their own render and commit it; a view
        staged here and never delivered is superseded by the next one.
        """
        pending = self._pending
        candidate = self._stage(disabled=disabled)
        if pending is not None:
            pending.view.stop()
        self._pending = candidate
        return candidate.view

    def _stage(self, *, disabled: bool = False) -> _Candidate:
        """Render and draw one candidate generation, publishing none of it.

        Runs no `on_load`, because it cannot: the paths that can await one stage through
        :meth:`_stage_loaded`, and the terminal and stage-only paths deliberately do not.
        """
        return self._draw(self.runtime.render(), disabled=disabled)

    def _draw(
        self,
        tree: ComponentTree,
        *,
        disabled: bool = False,
        profile: OperationRecorder | None = None,
    ) -> _Candidate:
        """Plan and draw one rendered tree into a candidate generation."""
        self._issued += 1
        generation = self._issued
        nodes = tree.nodes if self.status is None else (*tree.nodes, Status(self.status))
        rendered = Document(nodes, tree.assets, tree.document_key)
        handlers: dict[str, ActionBinding] = {}

        def draw() -> tuple[MountedView, Composition]:
            handlers.clear()

            def wire(node: SceneButton | SceneSelect, binding: ActionBinding) -> discord.ui.Item[Any]:
                key = binding.key
                handlers[key] = binding
                if isinstance(node, SceneButton):
                    item: discord.ui.Item[Any] = _WiredButton(node, self, key, generation)
                else:
                    item = _WiredSelect(node, self, key, generation)
                if disabled:
                    item.disabled = True  # pyrefly: ignore  # both wired types have the attribute
                return item

            def nav(state: NavigationState) -> Sequence[Node]:
                async def previous(event: PressEvent) -> None:
                    await self._move_cursor(state.key, -1)

                async def next_(event: PressEvent) -> None:
                    await self._move_cursor(state.key, 1)

                async def seek(page: int) -> None:
                    self._seek_cursor(state.key, page)

                # A materialized cursor always knows its own extent, so it can always seek.
                return self.nav(NavigationContext(state, previous, next_, seek))

            composition = compose(
                rendered,
                wire=wire,
                renderer=V2Renderer(
                    limits=self.limits,
                    view_factory=lambda: MountedView(self, self._remaining_timeout()),
                ),
                target=self.target,
                chrome=self._chrome,
                localization=self.localization,
                palette=self.palette,
                strict=self.strict,
                nav=nav,
                session=self.presentation,
                cache=self.runtime.plan_cache,
                profile=profile,
            )
            if not isinstance(composition.view, MountedView):
                message = "mounted Discord renderer returned the wrong view type"
                raise TypeError(message)
            return composition.view, composition

        view, composition = draw()
        assets = composition.assets
        if disabled:
            _disable_all(view)
        return _Candidate(
            view,
            composition,
            tree,
            handlers,
            composition.plan.form_bindings,
            generation,
            self.runtime.revision,
            assets,
            composition.plan.session_updates,
        )

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
            if self._finished:
                return None
            plan = self._plan
            if plan is None:
                return None
            generation = self._generation
            busy = busy_key is not None

            def wire(node: SceneButton | SceneSelect, binding: ActionBinding) -> discord.ui.Item[Any]:
                if isinstance(node, SceneButton):
                    if busy:
                        node = replace(
                            node,
                            disabled=True,
                            label=pending if binding.key == busy_key and pending is not None else node.label,
                        )
                    return _WiredButton(node, self, binding.key, generation)
                return _WiredSelect(replace(node, disabled=True) if busy else node, self, binding.key, generation)

            # No timeout on the paint: the committed view still owns the mount's idle timer,
            # and a second one would race it. For the same reason the paint is never
            # `stop()`ed -- it shares the committed generation's custom ids, so unregistering
            # it would take the live controls' dispatch entries with it.
            renderer = V2Renderer(limits=self.limits, view_factory=lambda: MountedView(self, None))
            view = renderer.view(plan.scene, plan=plan, wire=wire)
            if busy:
                _disable_all(view)
            return await self._write(DiscordPresentation.components_v2(view), keep_attachments=True, through=through)

    def _commit(self, candidate: _Candidate) -> None:
        """Publish a delivered candidate — the one place a render becomes the mount's state."""
        apply_updates(self.presentation, candidate.session_updates)
        self._handlers = candidate.handlers
        self._form_bindings = candidate.form_bindings
        self._generation = candidate.generation
        self.runtime.commit(candidate.tree, rendered_revision=candidate.revision)
        self._assets = candidate.assets
        self._plan = candidate.composition.plan
        candidate.view.timeout = self._remaining_timeout()
        self._dirty = self.runtime.dirty
        self._pending = None
        self._swap_view(candidate.view)
        # The commit point is where a mount becomes something a reader can see and click, and
        # so the first moment it is worth listing as live. Idempotent after the first.
        live.track(self)

    async def _commit_presented(self, candidate: _Candidate) -> None:
        """Commit one successfully delivered candidate and notify durability observers."""
        self._commit(candidate)
        for hook in tuple(self._presented_hooks):
            try:
                await hook(self)
            except Exception:
                logger.exception("presented hook failed for mount %s", self.id)

    def _rollback(self, candidate: _Candidate) -> None:
        """Discard an undelivered candidate; the message still shows the live generation.

        Nothing to unwind: planning only read the session, so dropping the candidate drops
        its presentation writes with it.
        """
        candidate.view.stop()
        self._dirty = True
        if self._pending is candidate:
            self._pending = None

    @property
    def presentation(self) -> PresentationSession:
        return self.runtime.presentation

    @presentation.setter
    def presentation(self, value: PresentationSession) -> None:
        self.runtime.presentation = value

    def _mark_dirty(self) -> None:
        self._dirty = True

    def invalidate(self) -> None:
        self.runtime.invalidate()

    def localize(self, localization: Localization) -> None:
        """Change the locale used by the next render of this live mount."""
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
    ) -> _Candidate:
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
            if _needs_load(root := self.runtime.root):
                if profile is None:
                    await self._load_all((root,))
                else:
                    with profile.span("component_load", attributes={"count": 1, "pass": pass_index}):
                        await self._load_all((root,))
                continue
            if profile is None:
                tree = self.runtime.render(defer=_needs_load)
            else:
                with profile.span("runtime_render", attributes={"pass": pass_index}):
                    tree = self.runtime.render(defer=_needs_load)
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
            atomic = self._pending_resources(tree, ResourceDelivery.ATOMIC)
            if atomic:
                if profile is None:
                    await self._settle_resources(atomic)
                else:
                    with profile.span(
                        "resource_settle.atomic",
                        attributes={"count": len(atomic), "pass": pass_index},
                    ):
                        await self._settle_resources(atomic)
                continue
            return self._draw(tree, disabled=disabled, profile=profile)
        message = f"mount {self.id}: component and resource loading did not settle in {_MAX_LOAD_PASSES} passes"
        raise LayoutInvariantError(message)

    @staticmethod
    def _pending_resources(tree: ComponentTree, delivery: ResourceDelivery) -> tuple[Resource[Any], ...]:
        return tuple(resource for resource in tree.resources if resource.delivery is delivery and resource.pending)

    async def _settle_resources(self, resources: Sequence[Resource[Any]]) -> None:
        """Settle one observed resource tier concurrently under this render operation."""
        if len(resources) == 1:
            await resources[0]._settle()
            return
        async with anyio.create_task_group() as tasks:
            for resource in resources:
                tasks.start_soon(resource._settle)

    async def _settle_visible(
        self,
        committed: _Candidate,
        *,
        through: deliver.EditHandle | None = None,
        profile: OperationRecorder | None = None,
    ) -> None:
        """Advance visible resources from their committed pending paint to settled paints."""
        candidate = committed
        for pass_index in range(_MAX_LOAD_PASSES):
            resources = self._pending_resources(candidate.tree, ResourceDelivery.VISIBLE)
            if not resources or self.runtime.dirty:
                return
            if self._handle is None or self._handle.expired():
                self._dirty = True
                return
            if profile is None:
                await self._settle_resources(resources)
            else:
                with profile.span(
                    "resource_settle.visible",
                    attributes={"count": len(resources), "pass": pass_index},
                ):
                    await self._settle_resources(resources)
            settled: _Candidate | None = None
            try:
                settled = await self._stage_loaded(profile=profile)
                wrote = await self._deliver(settled, through=through, profile=profile)
            except Exception:
                if settled is not None:
                    self._rollback(settled)
                logger.exception("mount %s could not deliver a settled resource render", self.id)
                return
            if wrote is None:
                self._rollback(settled)
                return
            if profile is None:
                await self._commit_presented(settled)
            else:
                with profile.span("commit"):
                    await self._commit_presented(settled)
            candidate = settled
        self._dirty = True
        logger.error(
            "mount %s: visible resources did not settle in %s passes",
            self.id,
            _MAX_LOAD_PASSES,
        )

    async def _load_all(self, components: Sequence[Component]) -> None:
        """Load one tier concurrently. A failure cancels its siblings; the render is doomed."""
        if len(components) == 1:
            # The overwhelmingly common case, and no group to unwrap.
            await self._load_one(components[0])
            return
        with _unwrapped():
            async with anyio.create_task_group() as tasks:
                for component in components:
                    tasks.start_soon(self._load_one, component)

    async def _load_one(self, component: Component) -> None:
        await component.on_load()
        component._loaded = True

    # --- Lifecycle ---------------------------------------------------------------------

    async def send(self, destination: deliver.Destination) -> deliver.SendResult:
        """Deliver this mount's first render through `destination`.

        The commit point for an initial send, and the same stage -> deliver -> commit sequence
        `flush` runs for an interaction edit: the host chooses where the message goes, the
        mount owns everything around the call. A destination that raises leaves the mount on
        its previous generation with the render still pending, so a second `send` is a clean
        retry.

        The structured result distinguishes a committed delivery, including a handle-less
        one, from a destination that deliberately abandoned delivery.
        """
        component = type(self.component)
        name = f"{component.__module__}.{component.__qualname__}"
        with self.profiler.operation(OperationKind.SEND, name=name, attributes={"mount_id": self.id}) as profile:
            with profile.span("render_lock"):
                await self._render_lock.acquire()
            try:
                if self._finished:
                    profile.set_result(TraceResult(TraceOutcome.ABANDONED, presentation=PresentationOutcome.ABANDONED))
                    return deliver.Abandoned()
                # A render staged by `_stage_view` and never delivered is superseded, not delivered.
                if self._pending is not None:
                    with profile.span("supersede"):
                        self._pending.view.stop()
                        self._pending = None
                # Component on_load and atomic resources settle first. Visible resources
                # deliberately make this the pending paint and settle after it commits.
                candidate = await self._stage_loaded(profile=profile)
                try:
                    destination_type = f"{type(destination).__module__}.{type(destination).__qualname__}"
                    with profile.span("discord_write", attributes={"destination": destination_type}):
                        receipt = await destination(candidate.presentation)
                except deliver.DeliveryAbandoned:
                    logger.debug("mount %s was not delivered: the destination abandoned it", self.id)
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    profile.set_result(TraceResult(TraceOutcome.ABANDONED, presentation=PresentationOutcome.ABANDONED))
                    return deliver.Abandoned()
                except Exception:
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    raise
                self._handle = receipt.handle
                if receipt.message is not None:
                    self._note_address(receipt.message)
                self._active = self.clock()
                with profile.span("commit"):
                    await self._commit_presented(candidate)
                await self._settle_visible(candidate, profile=profile)
                profile.set_result(TraceResult(TraceOutcome.COMPLETED, presentation=PresentationOutcome.WRITTEN))
                return deliver.Delivered(receipt)
            finally:
                self._render_lock.release()

    def _swap_view(self, view: MountedView) -> None:
        if self._view is not None and self._view is not view:
            self._view.stop()
        self._view = view

    async def _deliver(
        self,
        candidate: _Candidate,
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
        return await self._write(candidate.presentation, keep_attachments=not files, through=through, profile=profile)

    async def _write(
        self,
        presentation: DiscordPresentation,
        *,
        keep_attachments: bool,
        through: deliver.EditHandle | None,
        profile: OperationRecorder | None = None,
    ) -> deliver.EditHandle | None:
        """Write one presentation through the first usable handle, and say which one that was.

        `keep_attachments` leaves the message's files alone, which is what every edit that
        changes only controls wants.
        """
        for handle in (through, self._handle):
            if handle is None or handle.expired():
                continue
            try:
                if profile is None:
                    await handle.write(presentation, keep_attachments=keep_attachments)
                else:
                    source = "interaction" if handle is through else "standing"
                    handle_type = f"{type(handle).__module__}.{type(handle).__qualname__}"
                    with profile.span("discord_write", attributes={"source": source, "handle": handle_type}):
                        await handle.write(presentation, keep_attachments=keep_attachments)
            except deliver.StaleHandleError:
                logger.debug("mount %s discarded a stale edit handle", self.id, exc_info=True)
                if handle is self._handle:
                    self._handle = None
                continue
            return handle
        return None

    def _renew(self, interaction: discord.Interaction) -> None:
        """Trade up to the credentials this click carries.

        The bot's own never expire, so a mount holding them keeps them. Anything else is
        worth replacing: each interaction resets the clock, so a mount in use stays writable
        even when its message was only ever writable through the interaction that sent it.
        """
        if self._handle is not None and self._handle.permanent:
            return
        if (fresher := deliver.handle_from(interaction)) is not None:
            self._handle = fresher

    async def dispatch(
        self,
        key: str,
        interaction: discord.Interaction,
        values: list[str] | None = None,
        *,
        generation: int | None = None,
    ) -> None:
        """The funnel: finished check -> access policy -> handler -> flush."""
        kind = ActionKind.PRESS if values is None else ActionKind.SELECTION
        with self.profiler.operation(
            OperationKind.DISPATCH,
            name=key,
            attributes={"kind": kind.value, "mount_id": self.id},
        ) as operation:
            profile = _DispatchProfile(
                operation,
                interaction,
                GenerationDecision(generation, self._generation),
                operation.start_span("acknowledgement"),
            )
            try:
                if not await self._begin_dispatch(interaction, profile):
                    return
                with operation.span("binding"):
                    binding = self._handlers.get(key)
                if binding is None:
                    # A click raced a re-render that removed the control; acknowledge and move on.
                    profile.presentation = await self.flush(interaction, profile=profile)
                    profile.finish(DispatchDisposition.MISSING)
                    return
                if values is not None:
                    with operation.span("selection"):
                        binding = binding.routed(tuple(values))
                    if binding is None:
                        await self._acknowledge(interaction, profile=profile, source="invalid_selection")
                        profile.presentation = PresentationOutcome.ACKNOWLEDGED
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
                profile.action = ActionOutcome.CANCELLED
                profile.finish(DispatchDisposition.CANCELLED)
                raise
            except Exception as error:
                profile.presentation = PresentationOutcome.FAILED
                profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
                raise

    async def dispatch_submit(
        self,
        key: str,
        interaction: discord.Interaction,
        spec: FormSpec,
        values: Mapping[str, object],
        handler: SubmitHandler,
        *,
        policy: ActionPolicy = ActionPolicy.EXCLUSIVE,
        generation: int | None = None,
    ) -> None:
        """Route a modal submission through the same stale, action-policy, access, and flush funnel.

        Under `REBASE` this resolves the newest render-declared binding for `key`, the way a
        stale click does -- but only when that binding parses the same field keys, since a
        schema that has since changed shape cannot read what the reader actually typed. A form
        presented ad hoc from a handler has no render-time binding, and a trigger the newest
        render dropped has no newer one; both run what the reader submitted, because
        discarding a filled-in form is the worse of the two surprises.
        """
        with self.profiler.operation(
            OperationKind.DISPATCH,
            name=key,
            attributes={"kind": ActionKind.SUBMIT.value, "mount_id": self.id},
        ) as operation:
            profile = _DispatchProfile(
                operation,
                interaction,
                GenerationDecision(generation, self._generation),
                operation.start_span("acknowledgement"),
            )
            try:
                if not await self._begin_dispatch(interaction, profile):
                    return
                binding = _SubmitBinding(key, handler, policy, spec=spec)

                def rebase() -> ActionBinding | None:
                    newest = self._form_bindings.get(key)
                    if newest is None or newest.spec.field_keys != spec.field_keys:
                        return binding
                    return _SubmitBinding(key, newest.on_submit, policy, spec=newest.spec)

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
                profile.action = ActionOutcome.CANCELLED
                profile.finish(DispatchDisposition.CANCELLED)
                raise
            except Exception as error:
                profile.presentation = PresentationOutcome.FAILED
                profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
                raise

    async def _begin_dispatch(self, interaction: discord.Interaction, profile: _DispatchProfile) -> bool:
        # A mount sent through an unwaited interaction response never saw its own message; the
        # click is where it finally learns where it lives.
        self._note_address(interaction.message)
        if self._finished:
            # A finished mount can still be on screen with live controls: its disable-edit
            # may have failed, or a replacement may have taken over the session while this
            # message stayed visible. Say so rather than running a handler against state
            # nobody will see again.
            text = resolve_text(self.chrome.session_ended, self.localization).content
            await deliver.respond_text(interaction, text, ephemeral=True)
            profile.presentation = PresentationOutcome.WRITTEN
            profile.acknowledge("mount_finished")
            profile.finish(DispatchDisposition.MOUNT_FINISHED)
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
            profile.presentation = PresentationOutcome.WRITTEN
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

    def _event(self, interaction: discord.Interaction, values: list[str] | None) -> ActionEvent:
        """The portable event one Discord interaction becomes."""
        actor = Actor(str(interaction.user.id), getattr(interaction.user, "display_name", None))
        responder = ActionResponder(interaction, self)
        locale = self.localization.locale
        if values is None:
            return PressEvent(actor, responder, locale, {"frontend": "discord"})
        return SelectionEvent(actor, responder, locale, {"frontend": "discord"}, tuple(values))

    async def _admit(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: list[str] | None,
        profile: _DispatchProfile,
    ) -> bool:
        """Run this action's guard, answering the reader privately when it refuses.

        The access policy has already said this reader may use the panel; the guard says
        whether this press may run now. A denial writes nothing, bumps no generation, and
        opens no transaction -- it costs exactly one ephemeral message.
        """
        guard = binding.guard
        if guard is None:
            return True
        try:
            with profile.operation.span(
                "guard",
                attributes={"guard": f"{type(guard).__module__}.{type(guard).__qualname__}"},
            ):
                verdict = await guard.admit(self._event(interaction, values), self.guards.for_action(key))
        except Exception as error:
            await self.handle_error(interaction, error, f"guard:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.GUARD_FAILED, error)
            return False
        if verdict.allowed:
            return True
        reason = verdict.reason
        if reason is None:
            reason = (
                self.chrome.not_now if verdict.retry_after is None else self.chrome.try_again_in(verdict.retry_after)
            )
        await deliver.respond_text(interaction, self._chrome_text(reason), ephemeral=True)
        profile.presentation = PresentationOutcome.WRITTEN
        profile.acknowledge("guard_denied")
        profile.finish(DispatchDisposition.GUARD_DENIED)
        return False

    async def _dispatch_binding(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        generation: int | None,
        invoke: Callable[[ActionBinding, bool, int], Awaitable[None]],
        profile: _DispatchProfile,
        *,
        values: list[str] | None = None,
        rebase: Callable[[], ActionBinding | None] | None = None,
    ) -> None:
        if binding.policy in {ActionPolicy.IMMEDIATE, ActionPolicy.PARALLEL_READ}:
            rebased = False
            profile.decide_generation(self._generation)
            # No lock to be inside for these two, so admission is simply the last gate
            # before the handler, exactly as it is under the other policies.
            if not await self._admit(binding, key, interaction, values, profile):
                return
            await invoke(binding, rebased, self._generation)
            return

        with profile.operation.span("action_lock"):
            await self._action_lock.acquire()
        try:
            profile.decide_generation(self._generation)
            if binding.policy is ActionPolicy.EXCLUSIVE and generation not in {None, self._generation}:
                await self._acknowledge(interaction, profile=profile, source="stale")
                profile.presentation = PresentationOutcome.ACKNOWLEDGED
                profile.finish(DispatchDisposition.STALE)
                return
            rebased = binding.policy is ActionPolicy.REBASE and generation not in {None, self._generation}
            profile.decide_generation(self._generation, rebased=rebased)
            if binding.policy is ActionPolicy.REBASE and rebase is not None:
                # Resolved inside the lock: outside it, "newest" is whatever happened to be
                # committed before this action started waiting for its turn.
                with profile.operation.span("generation"):
                    refreshed = rebase()
                if refreshed is None:
                    await self._acknowledge(interaction, profile=profile, source="stale")
                    profile.presentation = PresentationOutcome.ACKNOWLEDGED
                    profile.finish(DispatchDisposition.STALE)
                    return
                binding = refreshed
            # Inside the lock, so a `when(...)` closure reads component state nobody is
            # writing, and before the transaction, so a denial writes nothing.
            if not await self._admit(binding, key, interaction, values, profile):
                return
            await invoke(binding, rebased, self._generation)
        finally:
            self._action_lock.release()

    async def _invoke(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: list[str] | None,
        submitted_generation: int | None,
        active_generation: int,
        rebased: bool,
        profile: _DispatchProfile,
    ) -> None:
        # One watchdog, two stages: an action carrying `Feedback` paints "working" at the
        # threshold, and that edit *is* the acknowledgement, so the deferral below never
        # fires for it. An action without feedback keeps the single-stage behaviour.
        busy = None if binding.feedback is None else _BusyPaint(self, key, binding.feedback, interaction)

        async def watchdog() -> None:
            remaining = self.acknowledgement_timeout
            if busy is not None:
                threshold = min(self.pending_after, remaining)
                await anyio.sleep(threshold)
                await busy.show(profile)
                remaining -= threshold
            await anyio.sleep(max(0.0, remaining))
            deferred = await self._acknowledge(interaction, profile=profile, source="watchdog")
            if deferred:
                profile.operation.mark_deadline_missed()

        with _unwrapped():
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(watchdog)
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
        values: list[str] | None,
        submitted_generation: int | None,
        active_generation: int,
        rebased: bool,
        profile: _DispatchProfile,
        busy: _BusyPaint | None = None,
    ) -> None:
        event = self._event(interaction, values)
        request = ActionRequest(
            event,
            key,
            ActionKind.PRESS if values is None else ActionKind.SELECTION,
            binding.policy,
            submitted_generation,
            active_generation,
            rebased,
        )

        async def handle() -> None:
            context = readonly_transaction() if binding.policy is ActionPolicy.PARALLEL_READ else transaction()
            with context:
                await binding.handler(event)

        try:
            handled = await self._run_middleware(request, handle, profile.operation)
        except Exception as error:
            profile.action = ActionOutcome.FAILED
            # Before the error hook: the failed action leaves no flush behind, so without
            # this the panel would sit on "working" with every control dead.
            restore = binding.feedback is not None and binding.feedback.restore_on_error
            if busy is not None and await busy.close() and restore:
                await busy.restore()
            await self.handle_error(interaction, error, f"action:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACTION_FAILED, error)
            return
        profile.action = ActionOutcome.HANDLED if handled else ActionOutcome.SHORT_CIRCUITED
        profile.acknowledge("action")
        self._renew(interaction)
        painted = busy is not None and await busy.close()
        try:
            profile.presentation = await self.flush(interaction, profile=profile)
        except Exception as error:
            profile.presentation = PresentationOutcome.FAILED
            profile.finish(DispatchDisposition.DELIVERY_FAILED, error)
            raise
        # An action that changed nothing flushes nothing, and a stranded "working" panel is
        # not a policy choice -- so this restore ignores `restore_on_error`.
        if painted and busy is not None and profile.presentation is not PresentationOutcome.WRITTEN:
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

        with _unwrapped():
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
            if evaluation.errors and spec.validation_policy is FormValidationPolicy.RETRY:
                await responder.retry_form(
                    spec.with_prefill(evaluation.attempted),
                    evaluation.errors,
                    key=key,
                    handler=binding.handler,
                    policy=binding.policy,
                    generation=self._generation if generation is None else generation,
                    actor_id=interaction.user.id,
                )
                profile.presentation = PresentationOutcome.WRITTEN
                profile.acknowledge("validation_retry")
                profile.finish(DispatchDisposition.VALIDATION_RETRY)
                return
            request = ActionRequest(
                event,
                key,
                ActionKind.SUBMIT,
                binding.policy,
                generation,
                active_generation,
                rebased,
            )

            async def handle() -> None:
                context = readonly_transaction() if binding.policy is ActionPolicy.PARALLEL_READ else transaction()
                with context:
                    await binding.handler(event)

            handled = await self._run_middleware(request, handle, profile.operation)
            profile.action = ActionOutcome.HANDLED if handled else ActionOutcome.SHORT_CIRCUITED
        except Exception as error:
            profile.action = ActionOutcome.FAILED
            await self.handle_error(interaction, error, f"form:{key}")
            profile.acknowledge("error_hook")
            profile.finish(DispatchDisposition.ACTION_FAILED, error)
            return
        profile.acknowledge("action")
        self._renew(interaction)
        try:
            profile.presentation = await self.flush(interaction, profile=profile)
        except Exception as error:
            profile.presentation = PresentationOutcome.FAILED
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

        async def invoke(index: int) -> None:
            nonlocal handled
            if index == len(self._middleware):
                handled = True
                with operation.span("handler"):
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
                with operation.span(f"middleware:{provenance}"):
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

    async def flush(
        self,
        interaction: discord.Interaction,
        *,
        profile: _DispatchProfile | None = None,
    ) -> PresentationOutcome:
        """Apply pending state changes as an interaction edit, or just acknowledge."""
        if profile is not None:
            with profile.operation.span("flush"):
                return await self._flush(interaction, profile.operation, dispatch=profile)
        with self.profiler.operation(
            OperationKind.DELIVERY, name="flush", attributes={"mount_id": self.id}
        ) as operation:
            try:
                presentation = await self._flush(interaction, operation)
            except Exception:
                operation.set_result(TraceResult(TraceOutcome.FAILED, presentation=PresentationOutcome.FAILED))
                raise
            outcome = (
                TraceOutcome.ABANDONED if presentation is PresentationOutcome.ABANDONED else TraceOutcome.COMPLETED
            )
            operation.set_result(TraceResult(outcome, presentation=presentation))
            return presentation

    async def _flush(
        self,
        interaction: discord.Interaction,
        operation: OperationRecorder,
        *,
        dispatch: _DispatchProfile | None = None,
    ) -> PresentationOutcome:
        acknowledge = False
        presentation = PresentationOutcome.NO_CHANGE
        with operation.span("render_lock"):
            await self._render_lock.acquire()
        try:
            if self._finished:
                return PresentationOutcome.ABANDONED
            if not self._dirty:
                acknowledge = True
            else:
                # A component cannot enter the tree without a state write, so a click that
                # changed nothing never reaches this at all.
                candidate = await self._stage_loaded(profile=operation)
                source = deliver.handle_from(interaction)
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
                    presentation = PresentationOutcome.ABANDONED
                else:
                    with operation.span("commit"):
                        await self._commit_presented(candidate)
                    await self._settle_visible(candidate, through=source, profile=operation)
                    presentation = PresentationOutcome.WRITTEN
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
            if presentation is PresentationOutcome.NO_CHANGE:
                presentation = PresentationOutcome.ACKNOWLEDGED
        return presentation

    async def finish_via(self, interaction: discord.Interaction) -> None:
        """Finish through an interaction edit — the shape a Close button wants."""
        run_hooks = False
        try:
            async with self._render_lock:
                if self._finished:
                    return
                # Marked before delivery: a failed disable-edit must not resurrect the mount.
                self._finished = True
                candidate = self._stage(disabled=True)
                source = deliver.handle_from(interaction)
                try:
                    wrote = await self._deliver(candidate, through=source, files=False)
                    # Editing through the interaction's own handle answers the click; nothing else
                    # does, whether it delivered through the standing handle or not at all.
                    if wrote is None or wrote is not source:
                        await self._acknowledge(interaction)
                except Exception:
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
                await self._run_finish_hooks()
            raise
        if run_hooks:
            await self._run_finish_hooks()

    async def refresh(self) -> None:
        """Out-of-band re-render (background state change, not an interaction).

        Shows the newest state at the next opportunity rather than this instant: a scheduler
        coalesces requests, and a mount with no live `handle` — an ephemeral message nobody
        has clicked lately — keeps the render in `pending` until someone clicks it again.
        """
        if self.scheduler is not None:
            self.scheduler.schedule(self)
            return
        await self.refresh_now()

    async def refresh_now(self, *, links: Sequence[TraceLink] = ()) -> None:
        component = type(self.component)
        name = f"{component.__module__}.{component.__qualname__}"
        with self.profiler.operation(
            OperationKind.REFRESH, name=name, attributes={"mount_id": self.id}, links=links
        ) as profile:
            with profile.span("render_lock"):
                await self._render_lock.acquire()
            try:
                if self._finished:
                    profile.set_result(TraceResult(TraceOutcome.ABANDONED, presentation=PresentationOutcome.ABANDONED))
                    return
                if self._handle is None or self._handle.expired():
                    self._dirty = True
                    profile.set_result(TraceResult(TraceOutcome.ABANDONED, presentation=PresentationOutcome.ABANDONED))
                    return
                candidate = await self._stage_loaded(profile=profile)
                try:
                    delivered = await self._deliver(candidate, profile=profile) is not None
                except Exception:
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    profile.set_result(TraceResult(TraceOutcome.FAILED, presentation=PresentationOutcome.FAILED))
                    raise
                if not delivered:
                    # `_rollback` leaves the mount dirty, so the next interaction shows this render.
                    # `refresh` has always promised the next opportunity rather than this instant.
                    with profile.span("rollback"):
                        self._rollback(candidate)
                    logger.debug("mount %s has no live edit handle; render deferred", self.id)
                    profile.set_result(TraceResult(TraceOutcome.ABANDONED, presentation=PresentationOutcome.ABANDONED))
                    return
                with profile.span("commit"):
                    await self._commit_presented(candidate)
                await self._settle_visible(candidate, profile=profile)
                profile.set_result(TraceResult(TraceOutcome.COMPLETED, presentation=PresentationOutcome.WRITTEN))
            finally:
                self._render_lock.release()

    def on_presented(self, callback: PresentedHook) -> None:
        """Observe future generations after Discord delivery and local commit both succeed."""
        self._presented_hooks.append(callback)

    def on_finish(self, callback: FinishHook) -> None:
        """Call `callback` once this mount has finished, after its teardown.

        Fires from every terminal path -- `finish`, `finish_via`, and the timeout that
        delegates to `finish` -- including one whose disable-edit failed. Callbacks run in
        registration order, and an exception is logged and swallowed: a broken observer must
        not abort another's cleanup, nor teardown itself.

        Calling `finish` from inside a hook is a no-op, so an observer that cascades to other
        mounts cannot loop back into this one. A hook registered on an already-finished mount
        never fires -- `finished` is the caller's to check first.
        """
        self._finish_hooks.append(callback)

    async def _run_finish_hooks(self) -> None:
        # Snapshotted because a hook may register another, which belongs to no firing.
        if self._hooks_fired:
            return
        self._hooks_fired = True
        for hook in tuple(self._finish_hooks):
            try:
                await hook(self)
            except Exception:
                logger.exception("finish hook failed for mount %s", self.id)

    async def finish(self, *, disable: bool = True) -> None:
        """Stop dispatching; optionally leave the message with its controls disabled."""
        run_hooks = False
        try:
            async with self._render_lock:
                if self._finished:
                    return
                self._finished = True
                try:
                    if disable and self._handle is not None:
                        candidate = self._stage(disabled=True)
                        try:
                            if await self._deliver(candidate, files=False) is None:
                                logger.debug("could not disable controls on finish: no live edit handle")
                                self._rollback(candidate)
                        except discord.HTTPException:
                            logger.debug("could not disable controls on finish", exc_info=True)
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
                await self._run_finish_hooks()
            raise
        if run_hooks:
            await self._run_finish_hooks()

    def _teardown(self) -> None:
        """Stop the live view and unmount the committed tree, once."""
        if self._view is not None:
            self._view.stop()
            self._view = None
        self.runtime.finish()

    async def handle_timeout(self) -> None:
        await self.finish(disable=True)

    async def handle_error(self, interaction: discord.Interaction, error: Exception, source: str) -> None:
        if self.on_error is not None:
            await self.on_error(interaction, error, source)
            return
        logger.error("unhandled component error in %s", source, exc_info=error)


def _disable_all(view: discord.ui.LayoutView) -> None:
    for item in view.walk_children():
        target = item.item if isinstance(item, discord.ui.DynamicItem) else item
        if isinstance(target, discord.ui.Button | discord.ui.Select) or hasattr(target, "disabled"):
            target.disabled = True  # pyrefly: ignore  # guarded by hasattr


def owned_mount(component: Component, user_id: int, **options: Any) -> Mount:
    """Construct a mount whose controls belong to one Discord user."""
    return Mount(component, access=Owner(user_id), **options)

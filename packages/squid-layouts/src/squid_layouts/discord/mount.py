"""The mount: one component bound to one Discord message.

Every interaction funnels through :meth:`Mount.dispatch` — author lock, handler, error hook,
and the re-render/edit cycle live here once instead of per view. The mount outlives its
discord.py views: each render produces a fresh :class:`MountedView`, and the previous one is
stopped after a successful edit so dispatch tables do not accumulate.
"""

import asyncio
import hashlib
import io
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, override

import anyio
import discord

from squid_layouts.actions import ActionBinding, ActionPolicy, Actor, PressEvent, SelectionEvent, SubmitEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME, LOCALIZATION_CONTEXT, Chrome, localize_chrome
from squid_layouts.discord import delivery as deliver
from squid_layouts.discord import live
from squid_layouts.discord.actions import ActionResponder
from squid_layouts.discord.compose import Composition, compose
from squid_layouts.discord.renderer import Renderer
from squid_layouts.document import Asset, Document, InlineAsset
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import FormSpec, FormValidationPolicy, SubmitHandler
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    NavFactory,
    NavigationContext,
    NavigationState,
    default_nav,
)
from squid_layouts.primitives.nodes import Node

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.runtime.component import Component, ComponentTree
from squid_layouts.runtime.owner import ComponentRuntime
from squid_layouts.runtime.presentation import PresentationSession, SessionUpdate, apply_updates
from squid_layouts.runtime.reactivity import readonly_transaction, transaction
from squid_layouts.scene.model import (
    PlanMetrics,
    PlanReport,
    PlanResult,
    SceneButton,
    SceneDocument,
    SceneSelect,
)
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization, resolve_text

logger = logging.getLogger(__name__)

_MAX_LOAD_PASSES = 16
"""Embedding tiers one delivery loads through -- not retries.

Each pass loads a tier and renders to reveal the next, so this bounds nesting depth. It only
trips on an `on_load` that keeps embedding freshly unloaded components, which is a loop rather
than a deep tree.
"""


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


class Scheduler(Protocol):
    """Anything that can absorb out-of-band refresh requests (see `Reactor`)."""

    def schedule(self, mount: Mount) -> None: ...


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


@dataclass(slots=True)
class _Candidate:
    """One staged render generation, which becomes the mount's state only when committed."""

    view: MountedView
    composition: Composition
    tree: ComponentTree
    handlers: dict[str, ActionBinding]
    generation: int
    assets: tuple[Asset, ...]
    # Presentation writes this render earned; a failed delivery simply drops them.
    session_updates: tuple[SessionUpdate, ...]


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
    """Seconds since the last render or click — what the timeout actually counts."""
    expires_in: float | None
    """Seconds of idle timeout left, or `None` for a mount that never times out."""
    lock_to: frozenset[int] | None
    handler_keys: tuple[str, ...]
    """Action keys the live generation answers to."""
    scene: SceneDocument | None
    report: PlanReport | None
    metrics: PlanMetrics | None


class Mount:
    """Binds a component to a message and owns its whole interaction lifecycle."""

    def __init__(
        self,
        component: Component,
        *,
        chrome: Chrome = DEFAULT_CHROME,
        localization: Localization = NEUTRAL,
        limits: V2Limits = LIMITS,
        strict: bool = False,
        timeout: float | None = 900,
        lock_to: int | AbstractSet[int] | None = None,
        on_error: ErrorHook | None = None,
        scheduler: Scheduler | None = None,
        nav: NavFactory | None = None,
        acknowledgement_timeout: float = 2.5,
    ) -> None:
        self.id = secrets.token_urlsafe(6)
        self.component = component
        # Diagnostics only. `_active` is what the idle timeout counts from: discord.py restarts
        # the view's timer on every click, and every commit hands it a brand new view.
        self._born = self._active = time.monotonic()
        self.address: MountAddress | None = None
        """Where this mount's message is, once it has one. Read `handle` to write to it."""
        self._chrome = chrome
        self.localization = localization
        self.chrome = localize_chrome(chrome, localization)
        self.nav = nav if nav is not None else default_nav
        self.runtime = ComponentRuntime(
            component,
            on_invalidate=self.invalidate,
            context={
                CHROME_CONTEXT: self.chrome,
                LOCALIZATION_CONTEXT: localization,
                NAV_FACTORY_CONTEXT: self.nav,
            },
        )
        self.acknowledgement_timeout = acknowledgement_timeout
        self.limits = limits
        self.strict = strict
        self.timeout = timeout
        # Normalized so dispatch has one shape to check. The single-owner call sites pass a
        # bare id and never see the difference.
        self.lock_to: frozenset[int] | None = (
            None if lock_to is None else frozenset((lock_to,) if isinstance(lock_to, int) else lock_to)
        )
        self.on_error = on_error
        self.scheduler = scheduler
        self._handle: deliver.EditHandle | None = None
        self._view: MountedView | None = None
        self._handlers: dict[str, ActionBinding] = {}
        self._action_lock = asyncio.Lock()
        self._generation = 0
        # Generations handed to staged renders: a candidate whose delivery failed must not
        # hand its control ids to the next one.
        self._issued = 0
        self._pending: _Candidate | None = None
        self._dirty = False
        self._finished = False
        self._finish_hooks: list[FinishHook] = []
        self._hooks_fired = False
        self._assets: tuple[Asset, ...] = ()
        self._plan: PlanResult | None = None

    @property
    def handle(self) -> deliver.EditHandle | None:
        """How this mount can write to its message right now, if it still can."""
        return self._handle

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
        idle = time.monotonic() - self._active
        component = type(self.component)
        return MountSnapshot(
            id=self.id,
            component=f"{component.__module__}.{component.__qualname__}",
            address=self.address,
            generation=self._generation,
            pending=self._dirty,
            finished=self._finished,
            age=time.monotonic() - self._born,
            idle=idle,
            expires_in=None if self.timeout is None else max(0.0, self.timeout - idle),
            lock_to=self.lock_to,
            handler_keys=tuple(sorted(self._handlers)),
            scene=None if self._plan is None else self._plan.scene,
            report=None if self._plan is None else self._plan.report,
            metrics=None if self._plan is None else self._plan.metrics,
        )

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

    def build_view(self, *, disabled: bool = False) -> MountedView:
        """Stage a render of the component's current state into a fresh view, committing none of it.

        The escape hatch for a host that only wants the rendered components — nothing here
        moves handlers, lifecycle hooks, page positions or the live generation. Delivery goes
        through :meth:`send` or :meth:`flush`, which stage their own render and commit it;
        a view staged here and never delivered is superseded by the next one.
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

    def _draw(self, tree: ComponentTree, *, disabled: bool = False) -> _Candidate:
        """Plan and draw one rendered tree into a candidate generation."""
        self._issued += 1
        generation = self._issued
        rendered = Document(tree.nodes, tree.assets, tree.document_key)
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

                return self.nav(NavigationContext(state, previous, next_))

            composition = compose(
                rendered,
                wire=wire,
                renderer=Renderer(
                    limits=self.limits,
                    view_factory=lambda: MountedView(self, self.timeout),
                ),
                limits=self.limits,
                chrome=self._chrome,
                localization=self.localization,
                strict=self.strict,
                nav=nav,
                session=self.presentation,
                cache=self.runtime.plan_cache,
            )
            if not isinstance(composition.view, MountedView):
                message = "mounted Discord renderer returned the wrong view type"
                raise TypeError(message)
            return composition.view, composition

        view, composition = draw()
        assets = tuple(
            asset
            for scene_asset in composition.plan.scene.assets
            if isinstance(asset := composition.plan.resources.get(f"asset:{scene_asset.key}"), Asset)
        )
        if disabled:
            _disable_all(view)
        return _Candidate(view, composition, tree, handlers, generation, assets, composition.plan.session_updates)

    def _commit(self, candidate: _Candidate) -> None:
        """Publish a delivered candidate — the one place a render becomes the mount's state."""
        apply_updates(self.presentation, candidate.session_updates)
        self._handlers = candidate.handlers
        self._generation = candidate.generation
        self.runtime.commit(candidate.tree)
        self._assets = candidate.assets
        self._plan = candidate.composition.plan
        self._active = time.monotonic()
        self._dirty = False
        self._pending = None
        self._swap_view(candidate.view)
        # The commit point is where a mount becomes something a reader can see and click, and
        # so the first moment it is worth listing as live. Idempotent after the first.
        live.track(self)

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

    def invalidate(self) -> None:
        self._dirty = True

    def localize(self, localization: Localization) -> None:
        """Change the locale used by the next render of this live mount."""
        self.localization = localization
        self.chrome = localize_chrome(self._chrome, localization)
        self.runtime.set_context(CHROME_CONTEXT, self.chrome)
        self.runtime.set_context(LOCALIZATION_CONTEXT, localization)
        self.invalidate()

    async def _move_cursor(self, key: str, delta: int) -> None:
        cursor = self.presentation.cursor(key)
        if 0 <= cursor.position.offset + delta < cursor.extent:
            self.presentation.move_cursor(key, Position(offset=cursor.position.offset + delta))
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

        A staged render's assets win, so files fetched alongside a `build_view()` belong to
        that render rather than to the generation it will replace.
        """
        return _attachment_files(self._pending.assets if self._pending is not None else self._assets)

    # --- Loading -----------------------------------------------------------------------

    async def _stage_loaded(self, *, disabled: bool = False) -> _Candidate:
        """Stage a candidate whose every component has completed its `on_load`.

        One pass per embedding tier: the root is known without rendering anything, and each
        tier's loaded render is what reveals the next. Siblings within a tier load together.
        A tier that still owes loads is never drawn -- only rendered, and only to find out
        who they are -- so an incomplete document is never planned. A tree that declares no
        loads is rendered and drawn exactly once, as it was before this existed.

        A raise leaves every completed load completed, every other one eligible to retry, and
        nothing staged, so the mount is exactly as deliverable as it was.
        """
        for _ in range(_MAX_LOAD_PASSES):
            if _needs_load(root := self.runtime.root):
                await self._load_all((root,))
                continue
            tree = self.runtime.render(defer=_needs_load)
            if not tree.deferred:
                return self._draw(tree, disabled=disabled)
            await self._load_all(tree.deferred)
        message = f"mount {self.id}: on_load did not settle in {_MAX_LOAD_PASSES} passes"
        raise LayoutInvariantError(message)

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

    async def send(self, destination: deliver.Destination) -> discord.Message | None:
        """Deliver this mount's first render through `destination`.

        The commit point for an initial send, and the same stage -> deliver -> commit sequence
        `flush` runs for an interaction edit: the host chooses where the message goes, the
        mount owns everything around the call. A destination that raises leaves the mount on
        its previous generation with the render still pending, so a second `send` is a clean
        retry.

        Returns the message when the receipt exposed one. `None` means only that no message
        object came back, or that the destination abandoned delivery; the receipt may still
        have committed a standing handle such as an interaction's `@original` authority.
        """
        if self._finished:
            return None
        # A render staged by `build_view` and never delivered is superseded, not delivered.
        if self._pending is not None:
            self._pending.view.stop()
            self._pending = None
        # The loaded render is the first one the reader sees: one delivery, no loading
        # paint. A raise here delivered nothing and staged nothing.
        candidate = await self._stage_loaded()
        try:
            receipt = await destination(candidate.view, _attachment_files(candidate.assets))
        except deliver.DeliveryAbandoned:
            logger.debug("mount %s was not delivered: the destination abandoned it", self.id)
            self._rollback(candidate)
            return None
        except Exception:
            self._rollback(candidate)
            raise
        self._handle = receipt.handle
        if receipt.message is not None:
            self._note_address(receipt.message)
        self._commit(candidate)
        return receipt.message

    def _swap_view(self, view: MountedView) -> None:
        if self._view is not None and self._view is not view:
            self._view.stop()
        self._view = view

    async def _deliver(
        self, candidate: _Candidate, *, through: deliver.EditHandle | None = None, files: bool = True
    ) -> deliver.EditHandle | None:
        """Show a staged render, through `through` when it is usable and the standing handle otherwise.

        Returns the handle that wrote, or `None` when none of them could. Which one it was
        matters: only the interaction's own handle answers the click as a side effect of
        editing, so a caller holding an interaction has to read this to know whether it still
        owes an acknowledgement.

        `files=False` leaves the message's attachments alone; a terminal disable-edit changes
        only the controls, and an empty asset set would otherwise strip them.
        """
        attachments = _attachment_files(candidate.assets) if files else None
        for handle in (through, self._handle):
            if handle is None or handle.expired():
                continue
            try:
                await handle.write(candidate.view, attachments=attachments)
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
        """The funnel: finished check -> author lock -> handler -> flush."""
        if not await self._begin_dispatch(interaction):
            return
        binding = self._handlers.get(key)
        if binding is None:
            # A click raced a re-render that removed the control; acknowledge and move on.
            await self.flush(interaction)
            return
        if values is not None:
            binding = binding.routed(tuple(values))
            if binding is None:
                await self._acknowledge(interaction)
                return
            key = binding.key

        async def invoke(current: ActionBinding) -> None:
            await self._invoke(current, key, interaction, values)

        await self._dispatch_binding(binding, key, interaction, generation, invoke, rebase=True)

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
        """Route a modal submission through the same stale, lock, policy, and flush funnel."""
        if not await self._begin_dispatch(interaction):
            return
        binding = ActionBinding(key, handler, policy)

        async def invoke(current: ActionBinding) -> None:
            await self._invoke_submit(current, key, interaction, spec, values, generation)

        await self._dispatch_binding(binding, key, interaction, generation, invoke)

    async def _begin_dispatch(self, interaction: discord.Interaction) -> bool:
        self._active = time.monotonic()
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
            return False
        if self.lock_to is not None and interaction.user.id not in self.lock_to:
            text = resolve_text(self.chrome.not_yours, self.localization).content
            await deliver.respond_text(interaction, text, ephemeral=True)
            return False
        return True

    async def _dispatch_binding(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        generation: int | None,
        invoke: Callable[[ActionBinding], Awaitable[None]],
        *,
        rebase: bool = False,
    ) -> None:
        if binding.policy in {ActionPolicy.IMMEDIATE, ActionPolicy.PARALLEL_READ}:
            await invoke(binding)
            return

        async with self._action_lock:
            if binding.policy is ActionPolicy.EXCLUSIVE and generation not in {None, self._generation}:
                await self._acknowledge(interaction)
                return
            if binding.policy is ActionPolicy.REBASE and rebase:
                refreshed = self._handlers.get(key)
                if refreshed is None:
                    await self._acknowledge(interaction)
                    return
                binding = refreshed
            await invoke(binding)

    async def _invoke(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: list[str] | None,
    ) -> None:
        async def watchdog() -> None:
            await anyio.sleep(self.acknowledgement_timeout)
            await self._acknowledge(interaction)

        with _unwrapped():
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(watchdog)
                await self._invoke_and_flush(binding, key, interaction, values)
                tasks.cancel_scope.cancel()

    async def _invoke_and_flush(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        values: list[str] | None,
    ) -> None:
        actor = Actor(str(interaction.user.id), getattr(interaction.user, "display_name", None))
        responder = ActionResponder(interaction, self)
        locale = self.localization.locale
        event = (
            PressEvent(actor, responder, locale, {"frontend": "discord"})
            if values is None
            else SelectionEvent(actor, responder, locale, {"frontend": "discord"}, tuple(values))
        )
        try:
            context = readonly_transaction() if binding.policy is ActionPolicy.PARALLEL_READ else transaction()
            with context:
                await binding.handler(event)
        except Exception as error:
            await self.handle_error(interaction, error, f"handler:{key}")
            return
        self._renew(interaction)
        await self.flush(interaction)

    async def _invoke_submit(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        spec: FormSpec,
        values: Mapping[str, object],
        generation: int | None,
    ) -> None:
        async def watchdog() -> None:
            await anyio.sleep(self.acknowledgement_timeout)
            await self._acknowledge(interaction)

        with _unwrapped():
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(watchdog)
                await self._invoke_submit_and_flush(binding, key, interaction, spec, values, generation)
                tasks.cancel_scope.cancel()

    async def _invoke_submit_and_flush(
        self,
        binding: ActionBinding,
        key: str,
        interaction: discord.Interaction,
        spec: FormSpec,
        values: Mapping[str, object],
        generation: int | None,
    ) -> None:
        try:
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
            else:
                context = readonly_transaction() if binding.policy is ActionPolicy.PARALLEL_READ else transaction()
                with context:
                    await binding.handler(event)
        except Exception as error:
            await self.handle_error(interaction, error, f"form:{key}")
            return
        self._renew(interaction)
        await self.flush(interaction)

    async def _acknowledge(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()

    async def flush(self, interaction: discord.Interaction) -> None:
        """Apply pending state changes as an interaction edit, or just acknowledge."""
        if self._finished:
            return
        if not self._dirty:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return
        # A component cannot enter the tree without a state write, so a click that changed
        # nothing never reaches this at all.
        candidate = await self._stage_loaded()
        source = deliver.handle_from(interaction)
        try:
            wrote = await self._deliver(candidate, through=source)
        except Exception:
            self._rollback(candidate)
            raise
        if wrote is None:
            self._rollback(candidate)
            await self._acknowledge(interaction)
            return
        self._commit(candidate)
        # Only the interaction's own handle answers the click by editing through it. Delivery
        # through the standing handle leaves the click unanswered, and Discord shows the user
        # "This interaction failed" three seconds later.
        if wrote is not source:
            await self._acknowledge(interaction)

    async def finish_via(self, interaction: discord.Interaction) -> None:
        """Finish through an interaction edit — the shape a Close button wants."""
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

    async def refresh_now(self) -> None:
        if self._finished or self._handle is None:
            return
        candidate = await self._stage_loaded()
        try:
            delivered = await self._deliver(candidate) is not None
        except Exception:
            self._rollback(candidate)
            raise
        if not delivered:
            # `_rollback` leaves the mount dirty, so the next interaction shows this render.
            # `refresh` has always promised the next opportunity rather than this instant.
            self._rollback(candidate)
            logger.debug("mount %s has no live edit handle; render deferred", self.id)
            return
        self._commit(candidate)

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
            # Neither the teardown nor the hooks are conditional on the disable-edit working,
            # or even on it failing in a way this anticipated. The mount is finished either
            # way, and an observer that never heard so would hold a dead mount forever.
            self._teardown()
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


def _attachment_files(assets: Sequence[Asset]) -> list[discord.File]:
    files: list[discord.File] = []
    for asset in assets:
        if not isinstance(asset.source, InlineAsset):
            message = f"Discord mount needs a host resolver for stored asset {asset.key!r}"
            raise TypeError(message)
        files.append(discord.File(io.BytesIO(asset.source.data), filename=asset.name))
    return files


def _disable_all(view: discord.ui.LayoutView) -> None:
    for item in view.walk_children():
        target = item.item if isinstance(item, discord.ui.DynamicItem) else item
        if isinstance(target, discord.ui.Button | discord.ui.Select) or hasattr(target, "disabled"):
            target.disabled = True  # pyrefly: ignore  # guarded by hasattr

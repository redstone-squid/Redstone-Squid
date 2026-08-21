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
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import discord

from squid_layouts.actions import ActionBinding, ActionPolicy, Actor, PressEvent, SelectionEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME, Chrome
from squid_layouts.discord import delivery as deliver
from squid_layouts.discord.actions import ActionResponder
from squid_layouts.discord.compose import Composition, compose
from squid_layouts.discord.renderer import Renderer
from squid_layouts.document import Asset, Document, InlineAsset
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.pagination import NavFactory, PageContext, default_nav
from squid_layouts.primitives.nodes import Node

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.runtime.component import Component, ComponentTree
from squid_layouts.runtime.owner import ComponentRuntime
from squid_layouts.runtime.presentation import PresentationSession, SessionUpdate, apply_updates
from squid_layouts.runtime.reactivity import readonly_transaction, transaction
from squid_layouts.scene.model import SceneButton, SceneSelect

logger = logging.getLogger(__name__)


class ErrorHook(Protocol):
    """Host-provided handler for exceptions escaping a component callback."""

    def __call__(self, interaction: discord.Interaction, error: Exception, source: str) -> Awaitable[None]: ...


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


class Mount:
    """Binds a component to a message and owns its whole interaction lifecycle."""

    def __init__(
        self,
        component: Component,
        *,
        chrome: Chrome = DEFAULT_CHROME,
        limits: V2Limits = LIMITS,
        strict: bool = False,
        timeout: float | None = 900,
        lock_to: int | None = None,
        on_error: ErrorHook | None = None,
        scheduler: Scheduler | None = None,
        nav: NavFactory | None = None,
        acknowledgement_timeout: float = 2.5,
    ) -> None:
        self.id = secrets.token_urlsafe(6)
        self.component = component
        self.chrome = chrome
        self.runtime = ComponentRuntime(component, on_invalidate=self.invalidate, context={CHROME_CONTEXT: chrome})
        self.nav = nav if nav is not None else default_nav(chrome)
        self.acknowledgement_timeout = acknowledgement_timeout
        self.limits = limits
        self.strict = strict
        self.timeout = timeout
        self.lock_to = lock_to
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
        self._assets: tuple[Asset, ...] = ()

    @property
    def handle(self) -> deliver.EditHandle | None:
        """How this mount can write to its message right now, if it still can."""
        return self._handle

    @property
    def pending(self) -> bool:
        """Whether a render is staged that Discord has not seen."""
        return self._dirty

    # --- Rendering ---------------------------------------------------------------------

    def build_view(self, *, disabled: bool = False) -> MountedView:
        """Stage a render of the component's current state into a fresh view.

        Staging is not committing: handlers, lifecycle hooks, page positions and the live
        generation only move in :meth:`bind`, once the host's own delivery has landed.
        Rendering the component tree is the one side effect staging cannot avoid.
        """
        pending = self._pending
        candidate = self._stage(disabled=disabled)
        if pending is not None:
            pending.view.stop()
        self._pending = candidate
        return candidate.view

    def _stage(self, *, disabled: bool = False) -> _Candidate:
        """Render and draw one candidate generation, publishing none of it."""
        self._issued += 1
        generation = self._issued
        tree = self.runtime.render()
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

            def nav(key: str, page: int, pages: int) -> Sequence[Node]:
                async def previous(event: PressEvent) -> None:
                    await self._move_page(key, -1)

                async def next_(event: PressEvent) -> None:
                    await self._move_page(key, 1)

                return self.nav(PageContext(key=key, page=page, pages=pages, on_prev=previous, on_next=next_))

            composition = compose(
                rendered,
                wire=wire,
                renderer=Renderer(
                    limits=self.limits,
                    view_factory=lambda: MountedView(self, self.timeout),
                ),
                limits=self.limits,
                chrome=self.chrome,
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
        self._dirty = False
        self._pending = None
        self._swap_view(candidate.view)

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

    async def _move_page(self, key: str, delta: int) -> None:
        cursor = self.presentation.cursor(key)
        if 0 <= cursor.index + delta < cursor.extent:
            self.presentation.move_cursor(key, cursor.index + delta)
            self.invalidate()

    def reset_page(self, key: str | None = None) -> None:
        """Forget one page position, or every position when key is omitted."""
        if key is None:
            self.presentation.reset_cursor()
        else:
            self.presentation.reset_cursor(key)
        self.invalidate()

    def attachment_files(self) -> list[discord.File]:
        """Materialize a fresh Discord file set from the current declarative assets.

        A staged render's assets win, so a host can send `build_view()` and its files
        together before the delivery that :meth:`bind` commits.
        """
        return _attachment_files(self._pending.assets if self._pending is not None else self._assets)

    # --- Lifecycle ---------------------------------------------------------------------

    async def send(self, destination: deliver.Destination) -> discord.Message | None:
        """Deliver this mount's first render through `destination`.

        The commit point for an initial send, and the same stage -> deliver -> commit sequence
        `flush` runs for an interaction edit: the host chooses where the message goes, the
        mount owns everything around the call. A destination that raises leaves the mount on
        its previous generation with the render still pending, so a second `send` is a clean
        retry.

        Returns the message when the destination produced one. `None` means the mount has no
        standing handle -- either the destination could not hand one back, in which case the
        first click mints one, or it abandoned the delivery and nothing was sent at all.
        """
        if self._finished:
            return None
        # A render staged by `build_view` and never delivered is superseded, not delivered.
        if self._pending is not None:
            self._pending.view.stop()
            self._pending = None
        candidate = self._stage()
        try:
            message = await destination(candidate.view, _attachment_files(candidate.assets))
        except deliver.DeliveryAbandoned:
            logger.debug("mount %s was not delivered: the destination abandoned it", self.id)
            self._rollback(candidate)
            return None
        except Exception:
            self._rollback(candidate)
            raise
        if message is not None:
            self._handle = deliver.handle_for(message)
        self._commit(candidate)
        return message

    def bind(self, message: discord.Message | None, view: MountedView) -> None:
        """Record the sent message and commit the render it carries.

        This is the commit point for the host-owned initial send: `build_view` stages only,
        so a delivery that never happened leaves the mount on its previous generation. Pass
        `None` for a view that reached Discord without giving the host a message handle; the
        mount keeps whatever edit handle it already had.
        """
        if message is not None:
            self._handle = deliver.handle_for(message)
        pending = self._pending
        if pending is not None and pending.view is view:
            self._commit(pending)
            return
        # A view this mount did not stage, or one a later stage superseded: it can be
        # recorded as live, but there is no candidate state to publish with it.
        self._swap_view(view)

    def _swap_view(self, view: MountedView) -> None:
        if self._view is not None and self._view is not view:
            self._view.stop()
        self._view = view

    async def _deliver(
        self, candidate: _Candidate, *, through: deliver.EditHandle | None = None, files: bool = True
    ) -> bool:
        """Show a staged render, through `through` when it is usable and the standing handle otherwise.

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
            return True
        return False

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
        """The funnel: author lock -> handler -> flush."""
        if self.lock_to is not None and interaction.user.id != self.lock_to:
            await deliver.respond_text(interaction, self.chrome.not_yours, ephemeral=True)
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
        if binding.policy in {ActionPolicy.IMMEDIATE, ActionPolicy.PARALLEL_READ}:
            await self._invoke(binding, key, interaction, values)
            return

        async with self._action_lock:
            if binding.policy is ActionPolicy.EXCLUSIVE and generation not in {None, self._generation}:
                await self._acknowledge(interaction)
                return
            if binding.policy is ActionPolicy.REBASE:
                binding = self._handlers.get(key)
                if binding is None:
                    await self._acknowledge(interaction)
                    return
            await self._invoke(binding, key, interaction, values)

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
        native_locale = getattr(interaction, "locale", None)
        locale = str(native_locale) if native_locale is not None else None
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
        candidate = self._stage()
        try:
            delivered = await self._deliver(candidate, through=deliver.handle_from(interaction))
        except Exception:
            self._rollback(candidate)
            raise
        if not delivered:
            self._rollback(candidate)
            await self._acknowledge(interaction)
            return
        self._commit(candidate)

    async def finish_via(self, interaction: discord.Interaction) -> None:
        """Finish through an interaction edit — the shape a Close button wants."""
        if self._finished:
            return
        # Marked before delivery: a failed disable-edit must not resurrect the mount.
        self._finished = True
        candidate = self._stage(disabled=True)
        try:
            if not await self._deliver(candidate, through=deliver.handle_from(interaction), files=False):
                await self._acknowledge(interaction)
        except Exception:
            self._rollback(candidate)
            raise
        finally:
            # The terminal tree is never committed, so `finish` unmounts the live one once.
            candidate.view.stop()
            self._teardown()

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
        candidate = self._stage()
        try:
            delivered = await self._deliver(candidate)
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

    async def finish(self, *, disable: bool = True) -> None:
        """Stop dispatching; optionally leave the message with its controls disabled."""
        if self._finished:
            return
        self._finished = True
        if disable and self._handle is not None:
            candidate = self._stage(disabled=True)
            try:
                if not await self._deliver(candidate, files=False):
                    logger.debug("could not disable controls on finish: no live edit handle")
                    self._rollback(candidate)
            except discord.HTTPException:
                logger.debug("could not disable controls on finish", exc_info=True)
                self._rollback(candidate)
            finally:
                candidate.view.stop()
        self._teardown()

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

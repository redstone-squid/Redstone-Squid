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
from typing import Any, Protocol

import anyio
import discord

from squid_layouts import deliver
from squid_layouts.actions import ActionBinding, ActionPolicy, Actor, PressEvent, SelectionEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME, Chrome

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.component import Component
from squid_layouts.compositor import Composition, compose
from squid_layouts.discord.actions import DiscordActionResponder
from squid_layouts.discord.renderer import DiscordRenderer
from squid_layouts.document import Asset, Document, InlineAsset
from squid_layouts.ir import Node
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.pagination import NavFactory, PageContext, default_nav
from squid_layouts.presentation import PresentationSession
from squid_layouts.reactivity import readonly_transaction, transaction
from squid_layouts.runtime import ComponentRuntime
from squid_layouts.scene import SceneButton, SceneSelect

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
        self.message: discord.Message | None = None
        self._view: MountedView | None = None
        self._handlers: dict[str, ActionBinding] = {}
        self._action_lock = asyncio.Lock()
        self._generation = 0
        self._dirty = False
        self._finished = False
        self._assets: tuple[Asset, ...] = ()

    # --- Rendering ---------------------------------------------------------------------

    def build_view(self, *, disabled: bool = False) -> MountedView:
        """Render the component's current state into a fresh view."""
        generation = self._generation + 1
        tree = self.runtime.render()
        rendered = Document(tree.nodes, tree.assets, tree.document_key)

        def draw() -> tuple[MountedView, Composition]:
            self._handlers = {}

            def wire(node: SceneButton | SceneSelect, binding: ActionBinding) -> discord.ui.Item[Any]:
                key = binding.key
                self._handlers[key] = binding
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
                renderer=DiscordRenderer(
                    limits=self.limits,
                    view_factory=lambda: MountedView(self, self.timeout),
                ),
                limits=self.limits,
                chrome=self.chrome,
                strict=self.strict,
                page={key: cursor.index for key, cursor in self.presentation.cursors.items()},
                nav=nav,
                session=self.presentation,
                cache=self.runtime.plan_cache,
            )
            if not isinstance(composition.view, MountedView):
                message = "mounted Discord renderer returned the wrong view type"
                raise TypeError(message)
            return composition.view, composition

        view, composition = draw()
        pagers = composition.plan.scene.pagers
        changed = {
            pager.key
            for pager in pagers
            if (cursor := self.presentation.cursor(pager.key)).content_fingerprint
            and cursor.content_fingerprint != pager.content_fingerprint
            and cursor.anchor is None
        }
        if changed:
            for key in changed:
                self.presentation.reset_cursor(key)
            view.stop()
            view, composition = draw()
            pagers = composition.plan.scene.pagers

        active = {pager.key for pager in pagers}
        for key in tuple(self.presentation.cursors):
            if key not in active:
                self.presentation.reset_cursor(key)
        for pager in pagers:
            cursor = self.presentation.cursor(pager.key)
            self.presentation.anchor_cursor(
                pager.key,
                pager.page,
                cursor.anchor,
                extent=pager.pages,
                content_fingerprint=pager.content_fingerprint,
            )
        self._generation = generation
        self.runtime.commit(tree)
        self._assets = tuple(
            asset
            for scene_asset in composition.plan.scene.assets
            if isinstance(asset := composition.plan.resources.get(f"asset:{scene_asset.key}"), Asset)
        )
        if disabled:
            _disable_all(view)
        self._dirty = False
        return view

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
        """Materialize a fresh Discord file set from the current declarative assets."""
        files: list[discord.File] = []
        for asset in self._assets:
            if not isinstance(asset.source, InlineAsset):
                message = f"Discord mount needs a host resolver for stored asset {asset.key!r}"
                raise TypeError(message)
            files.append(discord.File(io.BytesIO(asset.source.data), filename=asset.name))
        return files

    # --- Lifecycle ---------------------------------------------------------------------

    def bind(self, message: discord.Message, view: MountedView) -> None:
        """Record the sent message and the view generation currently live on it."""
        self.message = message
        self._swap_view(view)

    def _swap_view(self, view: MountedView) -> None:
        if self._view is not None and self._view is not view:
            self._view.stop()
        self._view = view

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
        responder = DiscordActionResponder(interaction, self)
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
        view = self.build_view()
        await deliver.apply_interaction(interaction, view, attachments=self.attachment_files())
        self._swap_view(view)

    async def finish_via(self, interaction: discord.Interaction) -> None:
        """Finish through an interaction edit — the shape a Close button wants."""
        if self._finished:
            return
        self._finished = True
        view = self.build_view(disabled=True)
        await deliver.apply_interaction(interaction, view)
        if self._view is not None:
            self._view.stop()
        view.stop()
        self.runtime.finish()

    async def refresh(self) -> None:
        """Out-of-band re-render (background state change, not an interaction)."""
        if self.scheduler is not None:
            self.scheduler.schedule(self)
            return
        await self.refresh_now()

    async def refresh_now(self) -> None:
        if self._finished or self.message is None:
            return
        view = self.build_view()
        self.message = await deliver.apply(self.message, view, attachments=self.attachment_files())
        self._swap_view(view)

    async def finish(self, *, disable: bool = True) -> None:
        """Stop dispatching; optionally leave the message with its controls disabled."""
        if self._finished:
            return
        self._finished = True
        if disable and self.message is not None:
            try:
                await deliver.apply(self.message, self.build_view(disabled=True))
            except discord.HTTPException:
                logger.debug("could not disable controls on finish", exc_info=True)
        if self._view is not None:
            self._view.stop()
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

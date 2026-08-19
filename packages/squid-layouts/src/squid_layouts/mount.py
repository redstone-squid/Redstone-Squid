"""The mount: one component bound to one Discord message.

Every interaction funnels through :meth:`Mount.dispatch` — author lock, handler, error hook,
and the re-render/edit cycle live here once instead of per view. The mount outlives its
discord.py views: each render produces a fresh :class:`MountedView`, and the previous one is
stopped after a successful edit so dispatch tables do not accumulate.
"""

import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import discord

from squid_layouts import deliver
from squid_layouts.actions import ActionBinding
from squid_layouts.chrome import DEFAULT_CHROME, Chrome

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.component import Component
from squid_layouts.compositor import compose
from squid_layouts.ir import Node
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.pagination import NavFactory, PageContext, default_nav
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


def _custom_id(mount_id: str, key: str) -> str:
    """A per-message-unique control id for ``key``, within Discord's 100-char limit.

    Nested components produce long dotted keys, and truncating those makes two controls
    collide — Discord rejects the message and, worse, a click could route to the wrong
    handler. Digest the key instead; dispatch itself goes by the in-process key.
    """
    custom_id = f"ctl:{mount_id}:{key}"
    if len(custom_id) <= 100:
        return custom_id
    return f"ctl:{mount_id}:#{hashlib.blake2s(key.encode()).hexdigest()[:12]}"


class _WiredButton(discord.ui.Button[MountedView]):
    def __init__(self, node: SceneButton, mount: Mount, key: str) -> None:
        super().__init__(
            style=getattr(discord.ButtonStyle, node.style.value),
            label=node.label,
            emoji=node.emoji,
            disabled=node.disabled,
            custom_id=_custom_id(mount.id, key),
        )
        self._mount = mount
        self._key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._mount.dispatch(self._key, interaction)


class _WiredSelect(discord.ui.Select[MountedView]):
    def __init__(self, node: SceneSelect, mount: Mount, key: str) -> None:
        super().__init__(
            placeholder=node.placeholder,
            min_values=node.min_values,
            max_values=node.max_values,
            disabled=node.disabled,
            custom_id=_custom_id(mount.id, key),
            options=[
                discord.SelectOption(
                    label=option.label, value=option.value, description=option.description, default=option.default
                )
                for option in node.options
            ],
        )
        self._mount = mount
        self._key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._mount.dispatch(self._key, interaction, self.values)


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
    ) -> None:
        self.id = secrets.token_urlsafe(6)
        self.component = component
        component._mount = self
        self.chrome = chrome
        self.nav = nav if nav is not None else default_nav(chrome)
        self.limits = limits
        self.strict = strict
        self.timeout = timeout
        self.lock_to = lock_to
        self.on_error = on_error
        self.scheduler = scheduler
        self.message: discord.Message | None = None
        self._view: MountedView | None = None
        self._handlers: dict[str, Callable[..., Awaitable[None]]] = {}
        self._dirty = False
        self._finished = False
        self._page: int | None = None  # adopts the pager's initial page on first render
        self._pages = 1
        self._staged_attachments: list[discord.File] | None = None

    # --- Rendering ---------------------------------------------------------------------

    def build_view(self, *, disabled: bool = False) -> MountedView:
        """Render the component's current state into a fresh view."""
        self._handlers = {}
        view = MountedView(self, self.timeout)

        def wire(node: SceneButton | SceneSelect, binding: ActionBinding) -> discord.ui.Item[Any]:
            key = binding.key
            self._handlers[key] = binding.handler
            if isinstance(node, SceneButton):
                item: discord.ui.Item[Any] = _WiredButton(node, self, key)
            else:
                item = _WiredSelect(node, self, key)
            if disabled:
                item.disabled = True  # pyrefly: ignore  # both wired types have the attribute
            return item

        def nav(page: int, pages: int) -> Sequence[Node]:
            context = PageContext(page=page, pages=pages, on_prev=self._page_prev, on_next=self._page_next)
            return self.nav(context)

        composition = compose(
            self.component.render(),
            into=view,
            wire=wire,
            limits=self.limits,
            chrome=self.chrome,
            strict=self.strict,
            page=self._page,
            nav=nav,
        )
        self._pages = composition.pages
        if composition.plan.scene.pagers:
            self._page = composition.page
        if disabled:
            _disable_all(view)
        self._dirty = False
        return view

    def invalidate(self) -> None:
        self._dirty = True

    async def _page_prev(self, interaction: discord.Interaction) -> None:
        if self._page is not None and self._page > 0:
            self._page -= 1
            self.invalidate()

    async def _page_next(self, interaction: discord.Interaction) -> None:
        if self._page is not None and self._page < self._pages - 1:
            self._page += 1
            self.invalidate()

    def reset_page(self) -> None:
        """Forget the page position, e.g. when the component switches to different content."""
        self._page = None
        self.invalidate()

    def set_attachments(self, files: Sequence[discord.File] | None) -> None:
        """Stage a replacement for the message's attachments, applied by the next flush.

        `[]` strips existing attachments; `None` (the default state) leaves them alone.
        """
        self._staged_attachments = None if files is None else list(files)
        self.invalidate()

    # --- Lifecycle ---------------------------------------------------------------------

    def bind(self, message: discord.Message, view: MountedView) -> None:
        """Record the sent message and the view generation currently live on it."""
        self.message = message
        self._swap_view(view)

    def _swap_view(self, view: MountedView) -> None:
        if self._view is not None and self._view is not view:
            self._view.stop()
        self._view = view

    async def dispatch(self, key: str, interaction: discord.Interaction, values: list[str] | None = None) -> None:
        """The funnel: author lock -> handler -> flush."""
        if self.lock_to is not None and interaction.user.id != self.lock_to:
            await deliver.respond_text(interaction, self.chrome.not_yours, ephemeral=True)
            return
        handler = self._handlers.get(key)
        if handler is None:
            # A click raced a re-render that removed the control; acknowledge and move on.
            await self.flush(interaction)
            return
        try:
            if values is None:
                await handler(interaction)
            else:
                await handler(interaction, values)
        except Exception as error:
            await self.handle_error(interaction, error, f"handler:{key}")
            return
        await self.flush(interaction)

    async def flush(self, interaction: discord.Interaction) -> None:
        """Apply pending state changes as an interaction edit, or just acknowledge."""
        if self._finished:
            return
        if not self._dirty:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return
        view = self.build_view()
        attachments = self._staged_attachments
        self._staged_attachments = None
        await deliver.apply_interaction(interaction, view, attachments=attachments)
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
        self.message = await deliver.apply(self.message, view)
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

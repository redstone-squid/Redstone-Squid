"""The mount: one component bound to one Discord message.

Every interaction funnels through :meth:`Mount.dispatch` — author lock, handler, error hook,
and the re-render/edit cycle live here once instead of per view. The mount outlives its
discord.py views: each render produces a fresh :class:`MountedView`, and the previous one is
stopped after a successful edit so dispatch tables do not accumulate.
"""

import hashlib
import logging
import secrets
from collections.abc import Awaitable, Sequence
from typing import Any, Protocol

import discord

from squid_layouts import deliver
from squid_layouts.actions import ActionBinding, Actor, PressEvent, SelectionEvent
from squid_layouts.chrome import DEFAULT_CHROME, Chrome

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.component import Component, render_component_tree
from squid_layouts.compositor import Composition, compose
from squid_layouts.discord.actions import DiscordActionResponder
from squid_layouts.ir import Node
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.pagination import NavFactory, PageContext, default_nav
from squid_layouts.reactivity import transaction
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
        self._handlers: dict[str, ActionBinding] = {}
        self._components: dict[str, Component] = {}
        self._dirty = False
        self._finished = False
        self._page: dict[str, int] = {}
        self._pages: dict[str, int] = {}
        self._pager_digests: dict[str, str] = {}
        self._staged_attachments: list[discord.File] | None = None

    # --- Rendering ---------------------------------------------------------------------

    def build_view(self, *, disabled: bool = False) -> MountedView:
        """Render the component's current state into a fresh view."""
        tree = render_component_tree(self.component)
        rendered = tree.nodes

        def draw() -> tuple[MountedView, Composition]:
            self._handlers = {}
            fresh = MountedView(self, self.timeout)

            def wire(node: SceneButton | SceneSelect, binding: ActionBinding) -> discord.ui.Item[Any]:
                key = binding.key
                self._handlers[key] = binding
                if isinstance(node, SceneButton):
                    item: discord.ui.Item[Any] = _WiredButton(node, self, key)
                else:
                    item = _WiredSelect(node, self, key)
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
                into=fresh,
                wire=wire,
                limits=self.limits,
                chrome=self.chrome,
                strict=self.strict,
                page=self._page,
                nav=nav,
            )
            return fresh, composition

        view, composition = draw()
        pagers = composition.plan.scene.pagers
        changed = {
            pager.key
            for pager in pagers
            if (previous := self._pager_digests.get(pager.key)) is not None and previous != pager.content_fingerprint
        }
        if changed:
            for key in changed:
                self._page.pop(key, None)
            view.stop()
            view, composition = draw()
            pagers = composition.plan.scene.pagers

        self._page = {pager.key: pager.page for pager in pagers}
        self._pages = {pager.key: pager.pages for pager in pagers}
        self._pager_digests = {pager.key: pager.content_fingerprint for pager in pagers}
        self._reconcile_components(tree.components)
        if disabled:
            _disable_all(view)
        self._dirty = False
        return view

    def invalidate(self) -> None:
        self._dirty = True

    async def _move_page(self, key: str, delta: int) -> None:
        page = self._page.get(key)
        pages = self._pages.get(key)
        if page is not None and pages is not None and 0 <= page + delta < pages:
            self._page[key] = page + delta
            self.invalidate()

    def reset_page(self, key: str | None = None) -> None:
        """Forget one page position, or every position when key is omitted."""
        if key is None:
            self._page.clear()
        else:
            self._page.pop(key, None)
        self.invalidate()

    def set_attachments(self, files: Sequence[discord.File] | None) -> None:
        """Stage a replacement for the message's attachments, applied by the next flush.

        `[]` strips existing attachments; `None` (the default state) leaves them alone.
        """
        self._staged_attachments = None if files is None else list(files)
        self.invalidate()

    def _reconcile_components(self, current: dict[str, Component]) -> None:
        removed = [
            (path, component) for path, component in self._components.items() if current.get(path) is not component
        ]
        added = [
            (path, component) for path, component in current.items() if self._components.get(path) is not component
        ]

        def depth(path: str) -> int:
            return 0 if path == "$" else path.count(".") + 1

        for path, component in sorted(removed, key=lambda item: depth(item[0]), reverse=True):
            component.on_unmount()
            if path != "$":
                component._parent = None
        for _, component in sorted(added, key=lambda item: depth(item[0])):
            component.on_mount()
        self._components = dict(current)

    def _unmount_components(self) -> None:
        for _path, component in sorted(
            self._components.items(),
            key=lambda item: 0 if item[0] == "$" else item[0].count(".") + 1,
            reverse=True,
        ):
            component.on_unmount()
            component._parent = None
        self._components.clear()
        self.component._mount = None

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
        binding = self._handlers.get(key)
        if binding is None:
            # A click raced a re-render that removed the control; acknowledge and move on.
            await self.flush(interaction)
            return
        try:
            actor = Actor(str(interaction.user.id), getattr(interaction.user, "display_name", None))
            responder = DiscordActionResponder(interaction, self)
            native_locale = getattr(interaction, "locale", None)
            locale = str(native_locale) if native_locale is not None else None
            event = (
                PressEvent(actor, responder, locale, {"frontend": "discord"})
                if values is None
                else SelectionEvent(actor, responder, locale, {"frontend": "discord"}, tuple(values))
            )
            with transaction():
                await binding.handler(event)
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
        self._unmount_components()

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
        self._unmount_components()

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

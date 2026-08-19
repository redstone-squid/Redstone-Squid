"""The mount: one component bound to one Discord message.

Every interaction funnels through :meth:`Mount.dispatch` — author lock, handler, error hook,
and the re-render/edit cycle live here once instead of per view. The mount outlives its
discord.py views: each render produces a fresh :class:`MountedView`, and the previous one is
stopped after a successful edit so dispatch tables do not accumulate.
"""

import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import discord

from squid_layouts import deliver
from squid_layouts.chrome import DEFAULT_CHROME, Chrome

# (deliver is imported as a module so tests can monkeypatch its functions.)
from squid_layouts.component import Component
from squid_layouts.conform import conform
from squid_layouts.ir import Button, Node, SelectMenu
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.materialize import materialize
from squid_layouts.solve import solve

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


class _WiredButton(discord.ui.Button[MountedView]):
    def __init__(self, node: Button, mount: Mount, key: str) -> None:
        super().__init__(
            style=node.style,
            label=node.label,
            emoji=node.emoji,
            disabled=node.disabled,
            custom_id=f"ctl:{mount.id}:{key}"[:100],
        )
        self._mount = mount
        self._key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._mount.dispatch(self._key, interaction)


class _WiredSelect(discord.ui.Select[MountedView]):
    def __init__(self, node: SelectMenu, mount: Mount, key: str) -> None:
        super().__init__(
            placeholder=node.placeholder,
            min_values=node.min_values,
            max_values=node.max_values,
            disabled=node.disabled,
            custom_id=f"ctl:{mount.id}:{key}"[:100],
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
    ) -> None:
        self.id = secrets.token_urlsafe(6)
        self.component = component
        component._mount = self
        self.chrome = chrome
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

    # --- Rendering ---------------------------------------------------------------------

    def build_view(self, *, disabled: bool = False) -> MountedView:
        """Render the component's current state into a fresh view."""
        rendered = self.component.render()
        nodes: Sequence[Node] = [rendered] if not isinstance(rendered, Sequence) else rendered
        solved = solve(list(nodes), limits=self.limits, chrome=self.chrome, strict=self.strict)
        self._handlers = {}
        view = MountedView(self, self.timeout)

        def wire(node: Button | SelectMenu, key: str) -> discord.ui.Item[Any]:
            if isinstance(node, Button):
                self._handlers[key] = node.on_click
                item: discord.ui.Item[Any] = _WiredButton(node, self, key)
            else:
                self._handlers[key] = node.on_select
                item = _WiredSelect(node, self, key)
            if disabled:
                item.disabled = True  # pyrefly: ignore  # both wired types have the attribute
            return item

        materialize(solved, into=view, wire=wire)
        if disabled:
            _disable_all(view)
        interventions = conform(view, strict=self.strict, limits=self.limits)
        if solved.notes or interventions:
            logger.warning("layout degraded: %s", "; ".join((*solved.notes, *interventions)))
        self._dirty = False
        return view

    def invalidate(self) -> None:
        self._dirty = True

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
        await deliver.apply_interaction(interaction, view)
        self._swap_view(view)

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

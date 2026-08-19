"""The Reactor: coalesced out-of-band re-renders.

Interaction-driven updates never come through here — they ride the interaction's own edit.
The Reactor only serves background state changes (a vote tally moving, a job finishing), and
coalesces them per mount: however many times a mount is invalidated while a render is queued
or in flight, it re-renders once with the latest state.

The host owns the task: start `Reactor.run()` under whatever supervises the process's
background work. This package never spawns tasks itself.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from squid_layouts.planning.cache import PlanCache
from squid_layouts.runtime.component import Component, ComponentTree, ContextKey, render_component_tree
from squid_layouts.runtime.presentation import PresentationSession

if TYPE_CHECKING:
    from squid_layouts.mount import Mount

logger = logging.getLogger(__name__)


class ComponentRuntime:
    """Frontend-neutral owner of a reactive component tree and presentation session."""

    def __init__(
        self,
        root: Component,
        *,
        presentation: PresentationSession | None = None,
        on_invalidate: Callable[[], None] | None = None,
        context: Mapping[ContextKey[Any], object] | None = None,
    ) -> None:
        self.root = root
        root._runtime = self
        self.presentation = presentation if presentation is not None else PresentationSession()
        self.on_invalidate = on_invalidate
        self.context = dict(context or {})
        self.plan_cache = PlanCache(32)
        self.components: dict[str, Component] = {}
        self.dirty = True

    def invalidate(self) -> None:
        self.dirty = True
        if self.on_invalidate is not None:
            self.on_invalidate()

    def render(self) -> ComponentTree:
        """Render a candidate tree; call :meth:`commit` after planning and drawing succeed."""
        return render_component_tree(self.root, runtime=self, context=self.context)

    def commit(self, tree: ComponentTree) -> None:
        """Publish one successfully planned tree and reconcile keyed lifecycle hooks."""

        def depth(path: str) -> int:
            return 0 if path == "$" else path.count(".") + 1

        removed = [
            (path, component)
            for path, component in self.components.items()
            if tree.components.get(path) is not component
        ]
        added = [
            (path, component)
            for path, component in tree.components.items()
            if self.components.get(path) is not component
        ]
        for path, component in sorted(removed, key=lambda item: depth(item[0]), reverse=True):
            component.on_unmount()
            component._runtime = None
            if path != "$":
                component._parent = None
        for _, component in sorted(added, key=lambda item: depth(item[0])):
            component.on_mount()
        self.components = dict(tree.components)
        self.dirty = False

    def finish(self) -> None:
        """Unmount the current tree from leaves to root."""
        for _path, component in sorted(
            self.components.items(),
            key=lambda item: 0 if item[0] == "$" else item[0].count(".") + 1,
            reverse=True,
        ):
            component.on_unmount()
            component._runtime = None
            component._parent = None
        self.components.clear()
        self.root._runtime = None


class Reactor:
    """Queue-driven refresh loop with per-mount coalescing."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Mount] = asyncio.Queue()
        self._queued: set[str] = set()

    def schedule(self, mount: Mount) -> None:
        """Enqueue a mount for refresh; a mount already queued is not queued twice."""
        if mount.id in self._queued:
            return
        self._queued.add(mount.id)
        self._queue.put_nowait(mount)

    async def run(self) -> None:
        """Serve refreshes until cancelled. Run this under the host's task supervisor."""
        while True:
            mount = await self._queue.get()
            self._queued.discard(mount.id)
            try:
                await mount.refresh_now()
            except Exception:
                logger.exception("mount refresh failed")

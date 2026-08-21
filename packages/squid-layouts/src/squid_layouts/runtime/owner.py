"""Frontend-neutral owner of a reactive component tree."""

from collections.abc import Callable, Mapping
from typing import Any

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.cache import PlanCache
from squid_layouts.runtime.component import Component, ComponentTree, render_component_tree
from squid_layouts.runtime.context import ContextKey
from squid_layouts.runtime.presentation import PresentationSession


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

    def set_context[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Replace one root context value for subsequent renders."""
        self.context[key] = value

    def render(self, *, defer: Callable[[Component], bool] | None = None) -> ComponentTree:
        """Render a candidate tree; call :meth:`commit` after planning and drawing succeed.

        ``defer`` renders for discovery only -- see :func:`render_component_tree`. Such a tree
        is missing subtrees and must never be passed to :meth:`commit`.
        """
        return render_component_tree(self.root, runtime=self, context=self.context, defer=defer)

    def commit(self, tree: ComponentTree) -> None:
        """Publish one successfully planned tree and reconcile keyed lifecycle hooks."""
        if tree.deferred:
            # A discovery tree is missing subtrees; committing one would unmount live
            # components that are only absent because expansion stopped early.
            message = "a discovery render cannot be committed"
            raise LayoutInvariantError(message)

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

"""Frontend-neutral owner of a reactive component tree."""

from collections.abc import Callable, Mapping
from typing import Any

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.cache import PlanCache, PlanMemo
from squid_layouts.runtime.component import (
    Component,
    ComponentTree,
    _ComponentRender,
    _ExpandedSubtree,
    render_component_tree,
)
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
        plan_cache: PlanCache | None = None,
    ) -> None:
        self.root = root
        root._runtime = self
        self.presentation = presentation if presentation is not None else PresentationSession()
        self.on_invalidate = on_invalidate
        self.context = dict(context or {})
        self.plan_cache = plan_cache if plan_cache is not None else PlanCache(32)
        self.plan_memo = PlanMemo()
        self.components: dict[str, Component] = {}
        self._render_cache: dict[Component, _ComponentRender] = {}
        self._subtree_cache: dict[str, _ExpandedSubtree] = {}
        self._dirty_components: set[Component] = set()
        self._forced_components: set[Component] = set()
        self._dirty_paths: set[str] = set()
        self._component_paths: dict[Component, str] = {}
        self._force_all = True
        self._candidate_tree: ComponentTree | None = None
        self._candidate_revision = -1
        self._committed_tree: ComponentTree | None = None
        self.revision = 0
        """Which render inputs the next render would see; a render captures it and `commit`
        compares against it. Not component state alone -- anything a render reads and a later
        commit could invalidate moves it, including a shared cell another owner wrote."""
        self.dirty = True
        """Whether the committed tree is behind the inputs a fresh render would read."""

    def invalidate(self, component: Component | None = None, *, check_dependencies: bool = False) -> None:
        """Declare the render inputs moved, so anything rendered before now is stale."""
        self.revision += 1
        self.dirty = True
        self._candidate_tree = None
        if component is None:
            self._force_all = True
        else:
            self._dirty_components.add(component)
            if not check_dependencies:
                self._forced_components.add(component)
            path = self._component_paths.get(component)
            if path is None:
                self._force_all = True
            else:
                self._dirty_paths.add(path)
                while path != "$":
                    path = "$" if "." not in path else path.rsplit(".", 1)[0]
                    self._dirty_paths.add(path)
        if self.on_invalidate is not None:
            self.on_invalidate()

    def set_context[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Replace one root context value for subsequent renders."""
        self.context[key] = value
        self.invalidate()

    def render(
        self,
        *,
        defer: Callable[[Component], bool] | None = None,
        reuse_committed: bool = False,
    ) -> ComponentTree:
        """Render a candidate tree; call :meth:`commit` after planning and drawing succeed.

        ``defer`` renders for discovery only -- see :func:`render_component_tree`. Such a tree
        is missing subtrees and must never be passed to :meth:`commit`.
        """
        if not self.dirty and not reuse_committed:
            # A direct render is an explicit request to sample inputs outside the reactive graph.
            # Mounted scheduler paths opt into the committed fast path after recording their cause.
            self.revision += 1
            self.dirty = True
            self._force_all = True
            self._candidate_tree = None
        if reuse_committed and self._candidate_tree is not None and self._candidate_revision == self.revision:
            return self._candidate_tree
        if reuse_committed and not self.dirty and self._committed_tree is not None:
            return self._committed_tree
        tree = render_component_tree(
            self.root,
            runtime=self,
            context=self.context,
            defer=defer,
            _render_cache=self._render_cache,
            _dirty=self._dirty_components,
            _forced=self._forced_components,
            _force_all=self._force_all,
            _subtree_cache=self._subtree_cache,
            _dirty_paths=self._dirty_paths,
        )
        self._dirty_components.clear()
        self._forced_components.clear()
        self._force_all = False
        self._dirty_paths.clear()
        if not tree.deferred:
            self._candidate_tree = tree
            self._candidate_revision = self.revision
        return tree

    def commit(self, tree: ComponentTree, *, rendered_revision: int | None = None) -> None:
        """Publish one successfully planned tree and reconcile keyed lifecycle hooks.

        `rendered_revision` is what :attr:`revision` was when `tree` was rendered. Delivery
        succeeding does not mean nothing changed while it was in flight, so the tree stays
        dirty when the inputs have moved since.
        """
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
        self._component_paths = {component: path for path, component in tree.components.items()}
        self._committed_tree = tree
        if rendered_revision is None or self.revision == rendered_revision:
            self._candidate_tree = tree
            self._candidate_revision = self.revision
        live = set(tree.components.values())
        self._render_cache = {
            component: snapshot for component, snapshot in self._render_cache.items() if component in live
        }
        self._subtree_cache = {
            path: snapshot
            for path, snapshot in self._subtree_cache.items()
            if tree.components.get(path) is snapshot.component
        }
        self.dirty = rendered_revision is not None and self.revision != rendered_revision

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
        self._render_cache.clear()
        self._subtree_cache.clear()
        self._dirty_components.clear()
        self._forced_components.clear()
        self._dirty_paths.clear()
        self._component_paths.clear()
        self._candidate_tree = None
        self._committed_tree = None
        self.plan_memo.clear()
        self.root._runtime = None

"""Expand components and splice dirty subtrees using caller-owned render caches.

Each expansion collects layout, assets, dependencies, and deferred children. It
produces a candidate tree without running mount or unmount lifecycle hooks.
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from squid_reactivity.core import observe_render
from squid_reactivity.internals import RENDER_OBSERVATION as _RENDER_OBSERVATION
from squid_reactivity.internals import AtomicResourcePending
from squid_reactivity.resources import AsyncBinding, observe_async_bindings, unique_async_bindings
from squid_reactivity.topics import Address
from squid_ui.context import ContextKey
from squid_ui.document import Asset, Document, DocumentLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.nodes import Boundary
from squid_ui.runtime._tree import (
    _IndexStep,
    _LayoutRoute,
    _map_layout_children_routed,
    _namespace,
    _SequenceStep,
)
from squid_ui.runtime.definition import _CURRENT_CONTEXT, AnyComponent, Component, RuntimeOwner
from squid_ui.runtime.tree import (
    ComponentTree,
    IncrementalRender,
    _ComponentRender,
    _ExpandedSubtree,
    _NodeSplice,
    _SpliceResult,
)
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import RenderTarget


def _inside(route: _LayoutRoute) -> _LayoutRoute:
    """Re-aim a sequence route at the element being descended into."""
    last = route[-1]
    if isinstance(last, _SequenceStep):
        return (*route[:-1], _IndexStep(last.field, last.start))
    return route


def _items[RenderTargetT: RenderTarget, ContextT = object](
    rendered: DocumentLike[RenderTargetT, ContextT], path: str
) -> tuple[tuple[LayoutNode[RenderTargetT, ContextT], ...], tuple[Asset, ...], str | None]:
    """Normalize whatever `render()` returned into nodes, assets, and a document key."""
    if isinstance(rendered, Document):
        if rendered.key is not None and path != "$":
            message = f"{path}: only the root component may return a keyed Document"
            raise LayoutInvariantError(message)
        return rendered.children, rendered.assets, rendered.key
    nodes = tuple(rendered) if isinstance(rendered, Sequence) else (rendered,)
    return nodes, (), None


def _same_context(left: dict[ContextKey[Any], object], right: dict[ContextKey[Any], object]) -> bool:
    return left.keys() == right.keys() and all(key.matches(value, right[key]) for key, value in left.items())


class _TreeRender[RenderTargetT: RenderTarget, ContextT = object]:
    """Expand one tree while collecting components, assets, dependencies, and async bindings.

    Instances are single-use. The supplied IncrementalRender owns caches that survive
    this expansion and allow unchanged subtrees to be reused.
    """

    def __init__(
        self,
        root: Component[RenderTargetT, ContextT],
        *,
        runtime: RuntimeOwner | None,
        context: dict[ContextKey[Any], object] | None,
        defer: Callable[[Component[RenderTargetT, ContextT]], bool] | None,
        incremental: IncrementalRender[RenderTargetT, ContextT],
    ) -> None:
        self.root = root
        self.runtime = runtime
        self.context = context
        self.defer = defer
        self.incremental = incremental
        self.components: dict[str, AnyComponent] = {}
        self.assets: list[Asset] = []
        self.document_key: str | None = None
        self.identities: dict[int, str] = {}
        self.active: set[int] = set()
        self.deferred: list[AnyComponent] = []
        self.observed_addresses: list[Address] = []
        self.observed_bindings: list[AsyncBinding] = []

    def run(self) -> ComponentTree[RenderTargetT, ContextT]:
        """Expand the tree, splicing in place when only part of it changed."""
        incremental = self.incremental
        spliced: _SpliceResult[RenderTargetT, ContextT] | None = None
        attempted_splices: set[AnyComponent] = set()
        try:
            spliced = _splice_dirty_subtrees(self.root, incremental, self.expand, attempted_splices)
            if spliced is None:
                # The splice found nothing reusable, so the partial state it left behind is
                # discarded rather than carried into the full expansion below.
                incremental.dirty.difference_update(attempted_splices)
                incremental.forced.difference_update(attempted_splices)
                self._reset()
                nodes = tuple(self.expand(self.root, "$", self.context or {}))
            else:
                nodes = spliced.subtree.nodes
        except AtomicResourcePending as pending:
            # Atomic state is never rendered while pending. Keep the resource observation from
            # the aborted discovery pass so the frontend can settle it before retrying.
            self.observed_bindings.append(pending.resource)
            nodes = ()
        if spliced is not None:
            return self._spliced_tree(spliced)
        # A deferred render has no complete root expansion, so there is no topology to carry.
        root_subtree = None if self.deferred else incremental.subtree_cache.get("$")
        return ComponentTree(
            nodes,
            self.components,
            tuple(self.assets),
            self.document_key,
            tuple(self.deferred),
            unique_async_bindings(self.observed_bindings),
            tuple(dict.fromkeys(self.observed_addresses)),
            root_subtree.topology if root_subtree is not None else object(),
        )

    def _reset(self) -> None:
        """Drop everything a failed splice attempt accumulated, in place."""
        self.components.clear()
        self.assets.clear()
        self.identities.clear()
        self.active.clear()
        self.deferred.clear()
        self.observed_addresses.clear()
        self.observed_bindings.clear()
        self.document_key = None

    def _spliced_tree(self, spliced: _SpliceResult[RenderTargetT, ContextT]) -> ComponentTree[RenderTargetT, ContextT]:
        """The tree a successful splice describes: the reused subtree plus what moved."""
        subtree = spliced.subtree
        for held in subtree.components.values():
            held._runtime = self.runtime
        return ComponentTree(
            subtree.nodes,
            subtree.components,
            subtree.assets,
            self.incremental.render_cache[self.root].document_key,
            (),
            subtree.async_bindings,
            subtree.observations,
            subtree.topology,
            spliced.base_topology,
            spliced.removed,
            spliced.added,
        )

    def rendered(
        self,
        component: Component[RenderTargetT, ContextT],
        path: str,
        inherited_context: dict[ContextKey[Any], object],
    ) -> _ComponentRender[RenderTargetT, ContextT]:
        """This component's render snapshot, reused when nothing it read has moved."""
        vars(component)["_ownership_sealed"] = True
        if (owner := component._owned_services) is not None:
            owner.seal()
        incremental = self.incremental
        render_cache = incremental.render_cache
        cached = render_cache.get(component)
        revision = vars(component).get("_state_revision", 0)
        dependency_check = component in incremental.dirty and component not in incremental.forced
        if (
            cached is not None
            and not incremental.force_all
            and component not in incremental.forced
            and cached.revision == revision
            and cached.root == (path == "$")
            and _same_context(cached.inherited_context, inherited_context)
            and (component not in incremental.dirty or (dependency_check and cached.observation.current()))
        ):
            current_addresses = cached.observation.addresses()
            cached.addresses = current_addresses
            self.observed_addresses.extend(current_addresses)
            self.observed_bindings.extend(cached.async_bindings)
            self.assets.extend(cached.assets)
            return cached

        local_context = dict(inherited_context)
        token = _CURRENT_CONTEXT.set(local_context)
        try:
            with observe_render() as observation, observe_async_bindings() as bindings:
                if active_observation := _RENDER_OBSERVATION.get():
                    active_observation.entering_own_render(component)
                value = component.render()
                own_nodes, own_assets, own_key = _items(value, path)
        finally:
            _CURRENT_CONTEXT.reset(token)
        snapshot = _ComponentRender[RenderTargetT, ContextT](
            revision,
            path == "$",
            dict(inherited_context),
            local_context,
            own_nodes,
            own_assets,
            own_key,
            observation,
            observation.addresses(),
            unique_async_bindings(bindings),
        )
        self.observed_addresses.extend(snapshot.addresses)
        self.observed_bindings.extend(snapshot.async_bindings)
        self.assets.extend(own_assets)
        if not any(isinstance(value, Component) for value in observation.constructed.values()):
            render_cache[component] = snapshot
        else:
            render_cache.pop(component, None)
        return snapshot

    def _reuse_subtree(
        self,
        path: str,
        component: Component[RenderTargetT, ContextT],
        inherited_context: dict[ContextKey[Any], object],
    ) -> _ExpandedSubtree[RenderTargetT, ContextT] | None:
        """The cached expansion at this path, if it still describes this component here."""
        incremental = self.incremental
        cached = incremental.subtree_cache.get(path)
        if (
            incremental.force_all
            or path in incremental.dirty_paths
            or cached is None
            or cached.component is not component
            or not _same_context(cached.inherited_context, inherited_context)
        ):
            return None
        return cached

    def expand(
        self,
        component: Component[RenderTargetT, ContextT],
        path: str,
        inherited_context: dict[ContextKey[Any], object],
    ) -> list[LayoutNode[RenderTargetT, ContextT]]:
        """Render this component and splice its children in, namespaced by boundary key."""
        identity = id(component)
        if identity in self.active:
            message = f"{path}: component embedding cycle"
            raise LayoutInvariantError(message)
        if previous := self.identities.get(identity):
            message = f"{path}: component instance is already embedded at {previous}"
            raise LayoutInvariantError(message)
        cached_subtree = self._reuse_subtree(path, component, inherited_context)
        if cached_subtree is not None:
            return self._adopt(cached_subtree)

        component_paths_before = set(self.components)
        asset_start = len(self.assets)
        binding_start = len(self.observed_bindings)
        observation_start = len(self.observed_addresses)
        deferred_start = len(self.deferred)
        self.identities[identity] = path
        self.components[path] = component
        component._runtime = self.runtime
        self.active.add(identity)
        embed_keys: set[str] = set()
        child_splices: dict[str, _NodeSplice] = {}
        snapshot = self.rendered(component, path, inherited_context)
        context = snapshot.child_context

        def expand_item(
            item: LayoutNode[RenderTargetT, ContextT], item_path: str, route: _LayoutRoute
        ) -> list[LayoutNode[RenderTargetT, ContextT]]:
            if isinstance(item, Boundary):
                if item.key in embed_keys:
                    message = f"{item_path}: duplicate Boundary key {item.key!r}"
                    raise LayoutInvariantError(message)
                embed_keys.add(item.key)
                if not isinstance(item.component, Component):
                    message = f"{item_path}: Boundary does not contain a Component"
                    raise LayoutInvariantError(message)
                # Before the defer check: a deferred child still reaches the mount through
                # its parent when its on_load writes state.
                child_component = item.component
                child_component._parent = component
                if self.defer is not None and self.defer(child_component):
                    self.deferred.append(child_component)
                    return []
                child_path = item.key if path == "$" else f"{path}.{item.key}"
                expanded = _namespace(self.expand(child_component, child_path, context), item.key)
                child_splices[child_path] = _NodeSplice(route, len(expanded), item.key)
                return expanded
            return [_map_layout_children_routed(item, item_path, _inside(route), expand_item)]

        try:
            nodes: list[LayoutNode[RenderTargetT, ContextT]] = []
            for index, item in enumerate(snapshot.nodes):
                nodes.extend(expand_item(item, f"{path}.{index}", (_SequenceStep(None, len(nodes)),)))
            if snapshot.document_key is not None:
                self.document_key = snapshot.document_key
            if len(self.deferred) == deferred_start:
                self.incremental.subtree_cache[path] = _ExpandedSubtree[RenderTargetT, ContextT](
                    component,
                    dict(inherited_context),
                    tuple(nodes),
                    {
                        component_path: held
                        for component_path, held in self.components.items()
                        if component_path not in component_paths_before
                    },
                    tuple(self.assets[asset_start:]),
                    unique_async_bindings(self.observed_bindings[binding_start:]),
                    tuple(dict.fromkeys(self.observed_addresses[observation_start:])),
                    child_splices,
                )
            else:
                # An incomplete expansion must never be reused: the deferred child is missing.
                self.incremental.subtree_cache.pop(path, None)
            return nodes
        finally:
            self.active.remove(identity)

    def _adopt(self, cached: _ExpandedSubtree[RenderTargetT, ContextT]) -> list[LayoutNode[RenderTargetT, ContextT]]:
        """Take a reused subtree's components and observations into this render."""
        for cached_path, cached_component in cached.components.items():
            cached_identity = id(cached_component)
            if previous := self.identities.get(cached_identity):
                message = f"{cached_path}: component instance is already embedded at {previous}"
                raise LayoutInvariantError(message)
            self.identities[cached_identity] = cached_path
            self.components[cached_path] = cached_component
            cached_component._runtime = self.runtime
        self.assets.extend(cached.assets)
        self.observed_bindings.extend(cached.async_bindings)
        self.observed_addresses.extend(cached.observations)
        return list(cached.nodes)


def render_component_tree[RenderTargetT: RenderTarget, ContextT = object](
    root: Component[RenderTargetT, ContextT],
    *,
    runtime: RuntimeOwner | None = None,
    context: dict[ContextKey[Any], object] | None = None,
    defer: Callable[[Component[RenderTargetT, ContextT]], bool] | None = None,
    incremental: IncrementalRender[RenderTargetT, ContextT] | None = None,
) -> ComponentTree[RenderTargetT, ContextT]:
    """Render and expand a component tree, preserving keyed component identity.

    ``defer`` makes this a *discovery* render: an embedded component it selects is recorded in
    :attr:`ComponentTree.deferred` and not expanded, so its :meth:`Component.render` is not
    called. That is what lets a frontend run :meth:`Component.on_load` before a component
    renders for the first time. The resulting tree is incomplete by construction; see
    :attr:`ComponentTree.deferred`.

    ``incremental`` is the caller's reuse state, updated in place. Omitting it renders cold.
    """
    return _TreeRender(
        root,
        runtime=runtime,
        context=context,
        defer=defer,
        incremental=incremental if incremental is not None else IncrementalRender(),
    ).run()


def _splice_dirty_subtrees[RenderTargetT: RenderTarget, ContextT = object](
    root: Component[RenderTargetT, ContextT],
    incremental: IncrementalRender[RenderTargetT, ContextT],
    expand: Callable[
        [Component[RenderTargetT, ContextT], str, dict[ContextKey[Any], object]],
        list[LayoutNode[RenderTargetT, ContextT]],
    ],
    attempted: set[AnyComponent],
) -> _SpliceResult[RenderTargetT, ContextT] | None:
    """Replace dirty cached subtrees without rerendering unaffected ancestors.

    Splicing requires stable node counts and unchanged assets, async bindings, and bus
    observations so cached parent metadata and child offsets remain valid. Return None
    when a full expansion is needed; attempted records work already rendered.
    """
    dirty = incremental.dirty
    component_paths = incremental.component_paths
    render_cache = incremental.render_cache
    subtree_cache = incremental.subtree_cache
    if incremental.force_all or not dirty or root not in render_cache:
        return None
    root_subtree = subtree_cache.get("$")
    if root_subtree is None:
        return None
    base_topology = root_subtree.topology
    removed_components: list[tuple[str, AnyComponent]] = []
    added_components: list[tuple[str, AnyComponent]] = []
    selected_paths = (
        {component: path for component in dirty if (path := component_paths.get(component)) is not None}
        if component_paths is not None
        else {snapshot.component: path for path, snapshot in subtree_cache.items() if snapshot.component in dirty}
    )
    if len(selected_paths) != len(dirty) or "$" in selected_paths.values():
        return None
    # Expand the highest dirty ancestor once; its expansion also visits dirty descendants.
    topmost = {
        component: path
        for component, path in selected_paths.items()
        if not any(
            ancestor is not component and (ancestor_path == "$" or path.startswith(f"{ancestor_path}."))
            for ancestor, ancestor_path in selected_paths.items()
        )
    }
    for component, path in sorted(topmost.items(), key=lambda item: item[1].count("."), reverse=True):
        previous = subtree_cache[path]
        candidate_nodes = tuple(expand(component, path, previous.inherited_context))
        attempted.update(
            candidate
            for candidate in dirty
            if candidate in render_cache
            and (candidate_path := selected_paths.get(candidate)) is not None
            and (candidate_path == path or candidate_path.startswith(f"{path}."))
        )
        candidate = subtree_cache.get(path)
        if candidate is None:
            return None
        removed_components.extend(
            (candidate_path, held)
            for candidate_path, held in previous.components.items()
            if candidate.components.get(candidate_path) is not held
        )
        added_components.extend(
            (candidate_path, held)
            for candidate_path, held in candidate.components.items()
            if previous.components.get(candidate_path) is not held
        )
        if candidate_nodes != candidate.nodes:
            candidate.nodes = candidate_nodes
        if not _same_subtree_presentation_metadata(previous, candidate) or len(previous.nodes) != len(candidate.nodes):
            return None
        # Propagate the replacement through cached ancestors without calling their render methods.
        previous_child = previous
        changed_child = candidate
        child_path = path
        while child_path != "$":
            parent_path = "$" if "." not in child_path else child_path.rsplit(".", 1)[0]
            parent = subtree_cache.get(parent_path)
            if (
                parent is None
                or parent.component not in render_cache
                or (splice := parent.child_splices.get(child_path)) is None
            ):
                return None
            replacement = tuple(_namespace(list(changed_child.nodes), splice.key))
            if len(replacement) != splice.count:
                return None
            same_topology = _same_component_map(previous_child.components, changed_child.components)
            if same_topology:
                changed_components = parent.components
            else:
                changed_components = dict(parent.components)
                for removed_path, removed in previous_child.components.items():
                    if changed_components.get(removed_path) is removed:
                        changed_components.pop(removed_path)
                changed_components.update(changed_child.components)
            changed_parent = _ExpandedSubtree[RenderTargetT, ContextT](
                parent.component,
                parent.inherited_context,
                _splice_nodes(parent.nodes, splice, replacement),
                changed_components,
                parent.assets,
                parent.async_bindings,
                parent.observations,
                parent.child_splices,
                parent.topology if same_topology else object(),
            )
            subtree_cache[parent_path] = changed_parent
            previous_child = parent
            changed_child = changed_parent
            child_path = parent_path
    if (root_subtree := subtree_cache.get("$")) is None:
        return None
    return _SpliceResult[RenderTargetT, ContextT](
        root_subtree,
        base_topology,
        tuple(removed_components),
        tuple(added_components),
    )


def _same_component_map(left: dict[str, AnyComponent], right: dict[str, AnyComponent]) -> bool:
    return left.keys() == right.keys() and all(left[path] is right[path] for path in left)


def _same_subtree_presentation_metadata[RenderTargetT: RenderTarget, ContextT = object](
    left: _ExpandedSubtree[RenderTargetT, ContextT], right: _ExpandedSubtree[RenderTargetT, ContextT]
) -> bool:
    return (
        left.assets == right.assets
        and left.async_bindings == right.async_bindings
        and left.observations == right.observations
    )


def _splice_nodes[RenderTargetT: RenderTarget, ContextT = object](
    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...],
    splice: _NodeSplice,
    replacement: tuple[LayoutNode[RenderTargetT, ContextT], ...],
) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
    def rewrite(value: Any, steps: _LayoutRoute) -> Any:
        step = steps[0]
        rest = steps[1:]
        if isinstance(step, _SequenceStep):
            sequence = value if step.field is None else getattr(value, step.field)
            changed = (*sequence[: step.start], *replacement, *sequence[step.start + splice.count :])
            return changed if step.field is None else replace(value, **{step.field: changed})
        if isinstance(step, _IndexStep):
            sequence = value if step.field is None else getattr(value, step.field)
            changed_item = replacement[0] if not rest else rewrite(sequence[step.index], rest)
            changed = (*sequence[: step.index], changed_item, *sequence[step.index + 1 :])
            return changed if step.field is None else replace(value, **{step.field: changed})
        changed_item = replacement[0] if not rest else rewrite(getattr(value, step.field), rest)
        return replace(value, **{step.field: changed_item})

    return rewrite(nodes, splice.route)

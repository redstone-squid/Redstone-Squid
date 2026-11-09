"""Build a layout tree from components and the state each component reads.

A Component renders layout nodes for its current state. It can include another
component through a Boundary, whose key identifies the child's place in the tree.
Expansion renders those children and replaces their boundaries with layout nodes,
ready for the planner to choose a target representation.

Rendering is synchronous and must not change state. The runtime records state
reads so it can reuse a component's layout when those inputs have not changed.
Replace state values rather than mutating them in place so writes are observable.

Frontends load components before their first render and deliver the planned
output. ComponentRuntime then commits the delivered tree and runs lifecycle hooks.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Generic, Protocol, TypeVar, cast, overload, override

from squid_reactivity.core import (
    Observation,
    StateOwner,
    observe_render,
)
from squid_reactivity.internals import RENDER_OBSERVATION as _RENDER_OBSERVATION
from squid_reactivity.internals import AtomicResourcePending
from squid_reactivity.resources import AsyncBinding, observe_async_bindings, unique_async_bindings
from squid_reactivity.topics import Address
from squid_ui.context import ContextKey
from squid_ui.document import Asset, Document, DocumentLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.constraints import Paginate
from squid_ui.primitives.nodes import (
    Boundary,
    Button,
    Code,
    ControlGroup,
    Footer,
    Heading,
    Lines,
    Row,
    Section,
    SelectMenu,
    Text,
)
from squid_ui.runtime._tree import (
    _IndexStep,
    _LayoutRoute,
    _map_layout_children_routed,
    _SequenceStep,
    map_layout_children,
)
from squid_ui.runtime.services import ManagedService, ServiceOwner
from squid_ui.semantic import (
    ActionControl as SemanticActionControl,
)
from squid_ui.semantic import (
    ActionControls as SemanticActionControls,
)
from squid_ui.semantic import (
    AnyLayoutNode,
    LayoutNode,
)
from squid_ui.semantic import (
    Choices as SemanticChoices,
)
from squid_ui.semantic import ControlGroup as SemanticControlGroup
from squid_ui.semantic import (
    Details as SemanticDetails,
)
from squid_ui.semantic import Download as SemanticDownload
from squid_ui.semantic import (
    FormTrigger as SemanticFormTrigger,
)
from squid_ui.semantic import Grid as SemanticGrid
from squid_ui.semantic import (
    Items as SemanticItems,
)
from squid_ui.semantic import (
    Link as SemanticLink,
)
from squid_ui.semantic import (
    List as SemanticList,
)
from squid_ui.semantic import (
    Media as SemanticMedia,
)
from squid_ui.semantic import (
    Navigation as SemanticNavigation,
)
from squid_ui.semantic import Paged as SemanticPaged
from squid_ui.semantic import Roster as SemanticRoster
from squid_ui.semantic import RoutedActionControl as SemanticRoutedActionControl
from squid_ui.semantic import (
    Table as SemanticTable,
)
from squid_ui.semantic import Toggle as SemanticToggle
from squid_ui.target_types import RenderTarget

type AnyComponent = Component[Any, Any]
"""A mounted component for any render target.

`Component`'s `RenderTargetT` defaults, so a bare `Component` annotation means `Component[RenderTarget]`
and rejects every other target -- including `Self` inside `Component`'s own methods. The tree
machinery below holds components for whatever target the caller mounted, so it says so, in the
same spirit as `AnyTarget`.
"""


class RuntimeOwner(Protocol):
    def invalidate(self, component: AnyComponent | None = None, *, check_dependencies: bool = False) -> None: ...


_CURRENT_CONTEXT: ContextVar[dict[ContextKey[Any], object] | None] = ContextVar(
    "squid_ui_component_context", default=None
)


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


ContextT = TypeVar("ContextT", contravariant=True, default=object)

RenderTargetT = TypeVar("RenderTargetT", contravariant=True, default=RenderTarget)
"""The dialects a component's rendered nodes can be drawn in.

Declared the old way, and contravariant on purpose, in a file that otherwise uses PEP 695.
`RenderTargetT` reaches `Component` through `DocumentLike[RenderTargetT, ContextT]`, which is contravariant
because `Renderable` puts the render target in a parameter position -- and neither pyrefly nor
basedpyright infers variance through that nesting. PEP 695 has no syntax for declaring variance,
so this is the only way to preserve portable-component substitution.
"""


@dataclass(frozen=True, slots=True)
class ComponentTree(Generic[RenderTargetT, ContextT]):
    """Layout nodes from a render, with the components that produced them.

    Child boundaries have been expanded into layout nodes; these nodes still need
    target planning before delivery. The component paths let ComponentRuntime
    determine which instances entered or left the delivered tree.
    """

    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    components: dict[str, AnyComponent]
    assets: tuple[Asset, ...] = ()
    document_key: str | None = None
    deferred: tuple[AnyComponent, ...] = ()
    """Children deferred for loading. A tree with deferred children is incomplete: do not plan, draw, or commit it."""
    async_bindings: tuple[AsyncBinding, ...] = ()
    """Async resources observed during rendering, including an atomic resource that suspended expansion."""
    observations: tuple[Address, ...] = ()
    """The bus addresses of every shared cell this render read, deduplicated.

    A frontend reconciles its subscriptions against this so a panel follows exactly what it
    currently looks at. Over-subscribing is the safe direction: a staged render that is later
    discarded costs at most one spurious refresh, while a missed subscription is a stale panel
    until someone clicks it.
    """
    _topology: object = field(default_factory=object, compare=False, repr=False)
    _topology_base: object | None = field(default=None, compare=False, repr=False)
    _removed_components: tuple[tuple[str, AnyComponent], ...] = field(default=(), compare=False, repr=False)
    _added_components: tuple[tuple[str, AnyComponent], ...] = field(default=(), compare=False, repr=False)


@dataclass(slots=True)
class _ComponentRender[RenderTargetT: RenderTarget, ContextT = object]:
    """One component's current pure render result and everything that read produced."""

    revision: int
    root: bool
    inherited_context: dict[ContextKey[Any], object]
    child_context: dict[ContextKey[Any], object]
    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    assets: tuple[Asset, ...]
    document_key: str | None
    observation: Observation
    addresses: tuple[Address, ...]
    async_bindings: tuple[AsyncBinding, ...]


@dataclass(slots=True)
class _ExpandedSubtree[RenderTargetT: RenderTarget, ContextT = object]:
    """A complete expansion below one stable component path, before parent namespacing."""

    component: AnyComponent
    inherited_context: dict[ContextKey[Any], object]
    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    components: dict[str, AnyComponent]
    assets: tuple[Asset, ...]
    async_bindings: tuple[AsyncBinding, ...]
    observations: tuple[Address, ...]
    child_splices: dict[str, _NodeSplice]
    topology: object = field(default_factory=object)


@dataclass(frozen=True, slots=True)
class _NodeSplice:
    route: _LayoutRoute
    count: int
    key: str


@dataclass(frozen=True, slots=True)
class _SpliceResult[RenderTargetT: RenderTarget, ContextT = object]:
    subtree: _ExpandedSubtree[RenderTargetT, ContextT]
    base_topology: object
    removed: tuple[tuple[str, AnyComponent], ...]
    added: tuple[tuple[str, AnyComponent], ...]


class Component(StateOwner, ABC, Generic[RenderTargetT, ContextT]):
    """A reusable part of an interface, combining state with a render method.

    render describes the current layout without changing state. State changes
    request another render, but planning and delivery belong to the frontend.
    Place child components with boundary and keep their keys stable across renders.
    Each boundary prefixes its child's control and presentation keys, so two child
    components can both use a local key such as "save" without colliding.

    A mounted frontend calls on_load before the first render. Once delivery
    succeeds, ComponentRuntime mounts added components and unmounts removed ones;
    owned services run for that mounted lifetime. Calling render_component_tree
    directly only expands the layout and does not run these lifecycle steps.
    """

    _runtime: RuntimeOwner | None = None
    _parent: AnyComponent | None = None
    _owned_services: ServiceOwner | None = None
    _loaded: bool = False
    """Whether this instance's :meth:`on_load` has completed. Owned by the frontend."""
    _reactive_internal_attributes = frozenset(
        {"_runtime", "_parent", "_loaded", "_state_revision", "_dependency_invalidation"}
    )

    @override
    def _state_changed(self, names: frozenset[str]) -> None:
        """React to committed writes to these state slots.

        Nothing is refreshed here. A computed and a resource each record what they read and
        re-check it when something asks for the value, so a commit only has to say the tree
        needs drawing again. `names` is for a subclass that wants to know which fields moved.
        """
        del names
        vars(self)["_dependency_invalidation"] = True
        try:
            self.invalidate()
        finally:
            vars(self).pop("_dependency_invalidation", None)

    @override
    def on_state_rollback(self) -> None:
        vars(self)["_state_revision"] = vars(self).get("_state_revision", 0) + 1

    event_frontend: ClassVar[str | None] = None
    """An explicit dispatch requirement; None leaves the component portable."""

    @abstractmethod
    def render(self) -> DocumentLike[RenderTargetT, ContextT]:
        """Return the current layout synchronously, without changing state."""

    def boundary(self, child: Component[RenderTargetT, ContextT], *, key: str) -> Boundary[RenderTargetT, ContextT]:
        """Place a child under a stable key used to namespace its controls and presentation state.

        Keys must be unique within this component's render, and one child instance cannot
        appear twice in the expanded tree. Child target and context requirements must be
        compatible with the parent.
        """
        return Boundary(child, key)

    async def on_load(self) -> None:
        """Fetch what this component cannot render without, before its first render.

        Runs once per instance, before the first delivery that would show it, and before
        :meth:`render` is ever called on it: a frontend stops expanding at an unloaded
        component rather than rendering it empty. Writes are ordinary pre-delivery state, so
        the delivered view is the loaded one -- there is no loading paint to design for.

        A raise leaves the instance eligible to retry on the next delivery attempt, and
        nothing is delivered in the meantime. Data the component can degrade without belongs
        in declared state with a render branch, refreshed by a handler, not here.

        Data that has to stay live belongs in a `sl.resource` instead. Because this runs once
        and under no consumer, its reads are untracked: `sl.watch()` here would follow
        nothing, and there would be no second run to reload it anyway.
        """

    def on_mount(self) -> None:
        """Run when this component enters the successfully delivered tree."""

    def on_unmount(self) -> None:
        """Run when this component leaves the delivered tree or the frontend finishes."""

    def own[ServiceT: ManagedService](self, service: ServiceT) -> ServiceT:
        """Give this component a service to run while it's mounted.

        Call this during setup, before the first render. It returns the same instance,
        ready to store on the component and share through ``provide``. The service
        starts after a successful delivery and stops when the component unmounts.

        Remounting uses the same instance and starts a fresh run after cleanup finishes.
        """
        if vars(self).get("_ownership_sealed", False):
            msg = "component service ownership must be declared before rendering"
            raise RuntimeError(msg)
        owner = self._owned_services
        if owner is None:
            owner = ServiceOwner()
            vars(self)["_owned_services"] = owner
        return owner.own(service)

    @override
    def invalidate(self) -> None:
        """Mark this component's message as needing a re-render."""
        dependency = vars(self).get("_dependency_invalidation", False)
        if not dependency:
            vars(self)["_state_revision"] = vars(self).get("_state_revision", 0) + 1
        if self._runtime is not None:
            self._runtime.invalidate(self, check_dependencies=dependency)
        elif self._parent is not None:
            self._parent.invalidate()

    def provide[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Provide a value to descendants for this render.

        Providing a service shares access; it neither owns nor starts the service.
        Descendants can receive it before its run is ready.
        """
        context = _CURRENT_CONTEXT.get()
        if context is None:
            message = "provide() is only available while rendering"
            raise RuntimeError(message)
        context[key] = value

    @overload
    def inject[ValueT](self, key: ContextKey[ValueT]) -> ValueT: ...

    @overload
    def inject[ValueT](self, key: ContextKey[ValueT], default: ValueT) -> ValueT: ...

    def inject[ValueT](self, key: ContextKey[ValueT], default: ValueT | _Missing = _MISSING) -> ValueT:
        """Read the nearest provided value while rendering."""
        context = _CURRENT_CONTEXT.get()
        if context is not None and key in context:
            return cast(ValueT, context[key])
        if not isinstance(default, _Missing):
            return default
        message = f"no value was provided for context key {key.name!r}"
        raise LookupError(message)


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


@dataclass(slots=True)
class IncrementalRender[RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
    """Keep reusable renders and track which components need another render.

    render_cache stores individual component results; subtree_cache stores results
    with their children already expanded. Dirty sets identify which cached results
    need checking or replacement. ComponentRuntime retains these collections across
    renders, and tree expansion updates them in place.
    """

    render_cache: dict[AnyComponent, _ComponentRender[RenderTargetT, ContextT]] = field(default_factory=dict)
    """Each component's last render snapshot, keyed by instance."""
    dirty: set[AnyComponent] = field(default_factory=set)
    forced: set[AnyComponent] = field(default_factory=set)
    """Dirty components whose dependencies must not be consulted -- re-render regardless."""
    force_all: bool = False
    subtree_cache: dict[str, _ExpandedSubtree[RenderTargetT, ContextT]] = field(default_factory=dict)
    dirty_paths: set[str] = field(default_factory=set)
    component_paths: dict[AnyComponent, str] | None = None
    """Where each component sat last time, for locating a splice. None disables splicing."""


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


def _namespace[RenderTargetT: RenderTarget, ContextT = object](
    nodes: list[LayoutNode[RenderTargetT, ContextT]], prefix: str
) -> list[LayoutNode[RenderTargetT, ContextT]]:
    """Rewrite an embedded subtree's control keys under ``prefix``.

    Explicit control keys are scoped under the embed path, so inserting a sibling cannot
    change the identity of any existing action.
    """

    def key_for(node: Button | SelectMenu) -> str:
        return f"{prefix}.{node.key}"

    def rewrite_text[T: Text | Heading | Footer | Code | Lines](node: T) -> T:
        overflow = node.overflow
        if isinstance(overflow, Paginate) and overflow.key is not None:
            return replace(node, overflow=replace(overflow, key=f"{prefix}.{overflow.key}"))
        return node

    def rewrite_item[T](item: T) -> T:
        return replace(item, key=key_for(item)) if isinstance(item, Button) else item  # pyrefly: ignore

    def rewrite_semantic_control(
        item: SemanticActionControl | SemanticLink | SemanticRoutedActionControl,
    ) -> SemanticActionControl | SemanticLink | SemanticRoutedActionControl:
        return replace(item, key=f"{prefix}.{item.key}")

    def rewrite_semantic_action(
        item: SemanticActionControl | SemanticLink | SemanticRoutedActionControl | SemanticControlGroup,
    ) -> SemanticActionControl | SemanticLink | SemanticRoutedActionControl | SemanticControlGroup:
        if isinstance(item, SemanticControlGroup):
            return replace(
                item,
                key=f"{prefix}.{item.key}",
                controls=tuple(rewrite_semantic_control(control) for control in item.controls),
            )
        return rewrite_semantic_control(item)

    def rewrite(node: AnyLayoutNode) -> AnyLayoutNode:
        match node:
            case SemanticActionControls(items=items, key=key):
                return replace(
                    node,
                    key=f"{prefix}.{key}",
                    items=tuple(rewrite_semantic_action(item) for item in items),
                )
            case SemanticDetails(key=key) | SemanticItems(key=key):
                keyed = replace(node, key=f"{prefix}.{key}")
                return map_layout_children(keyed, "$", lambda child, _path: (rewrite(child),))
            case (
                SemanticList(key=key)
                | SemanticChoices(key=key)
                | SemanticNavigation(key=key)
                | SemanticTable(key=key)
                | SemanticGrid(key=key)
                | SemanticRoster(key=key)
                | SemanticMedia(key=key)
                | SemanticFormTrigger(key=key)
                | SemanticToggle(key=key)
                | SemanticDownload(key=key)
            ):
                return replace(node, key=f"{prefix}.{key}")
            case SemanticPaged(key=key):
                keyed = replace(node, key=f"{prefix}.{key}")
                return map_layout_children(keyed, "$", lambda child, _path: (rewrite(child),))
            case Text() | Heading() | Footer() | Code() | Lines():
                return rewrite_text(node)
            case Row(items=items):
                return Row(items=tuple(rewrite_item(item) for item in items))
            case ControlGroup(items=items):
                return ControlGroup(items=tuple(rewrite_item(item) for item in items))
            case Section(texts=texts, accessory=accessory):
                return Section(texts=tuple(rewrite_text(text) for text in texts), accessory=rewrite_item(accessory))
            case SelectMenu():
                return replace(node, key=key_for(node))
            case _:
                return map_layout_children(node, "$", lambda child, _path: (rewrite(child),))

    # `rewrite` is written over the erased union: it matches on primitive shapes, none of which
    # carry a target, and returns the same nodes rekeyed. That costs the local walker's
    # precision, not the caller's, so the target the caller passed in is what comes back.
    return cast(list[LayoutNode[RenderTargetT, ContextT]], [rewrite(node) for node in nodes])

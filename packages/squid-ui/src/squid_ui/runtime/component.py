"""Stateful components: render() is a pure function of state; mutating state re-renders.

A component describes *what the message should say now*. Interaction callbacks assign
declared state, and every assignment schedules the mount to re-render. State values are
immutable and replaced rather than mutated. Components never touch discord.py objects
directly.

Components compose through explicit keyed Boundary nodes, so two instances of the same
child class can appear in one message without their controls or pagers cross-wiring. Only the
root component is attached to a Mount; children reach it through their parent.
"""

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Protocol

from squid_ui.document import Asset, Document
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.constraints import Paginate
from squid_ui.primitives.nodes import (
    ActionGroup,
    Boundary,
    Button,
    Code,
    Footer,
    Heading,
    Lines,
    Row,
    Section,
    SelectMenu,
    Text,
)
from squid_ui.runtime._tree import map_layout_children
from squid_ui.runtime.context import ContextKey
from squid_ui.runtime.resources import (
    AsyncBinding,
    _AtomicResourcePending,
    observe_async_bindings,
    unique_async_bindings,
)
from squid_ui.runtime.topics import Address
from squid_ui.semantic import (
    Action as SemanticAction,
)
from squid_ui.semantic import (
    ActionGroup as SemanticActionGroup,
)
from squid_ui.semantic import (
    Actions as SemanticActions,
)
from squid_ui.semantic import (
    Choices as SemanticChoices,
)
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
    LayoutNode,
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
from squid_ui.semantic import (
    Table as SemanticTable,
)
from squid_ui.semantic import Toggle as SemanticToggle
from squid_reactivity.core import (
    _RENDER_OBSERVATION,
    Observation,
    StateOwner,
    observe_render,
)

type RenderNode[ModeT = Any] = LayoutNode[ModeT]
type RenderResult[ModeT = Any] = Document[ModeT] | LayoutNode[ModeT] | Sequence[LayoutNode[ModeT]]


class RuntimeOwner(Protocol):
    def invalidate(self, component: Component | None = None, *, check_dependencies: bool = False) -> None: ...


_CURRENT_CONTEXT: ContextVar[dict[ContextKey[Any], object] | None] = ContextVar(
    "squid_ui_component_context", default=None
)
_MISSING = object()


@dataclass(frozen=True, slots=True)
class ComponentTree:
    """One expanded render and the component identities that produced it."""

    nodes: tuple[LayoutNode, ...]
    components: dict[str, Component]
    assets: tuple[Asset, ...] = ()
    document_key: str | None = None
    deferred: tuple[Component, ...] = ()
    async_bindings: tuple[AsyncBinding, ...] = ()
    """Embedded components expansion stopped at, in the order it met them.

    Only ever non-empty for a discovery render (see ``render_component_tree``'s ``defer``).
    Such a tree is missing whole subtrees, so it describes what to load next and nothing else:
    never plan it, draw it, or commit it.
    """
    observations: tuple[Address, ...] = ()
    """The bus addresses of every shared cell this render read, deduplicated.

    A frontend reconciles its subscriptions against this so a panel follows exactly what it
    currently looks at. Over-subscribing is the safe direction: a staged render that is later
    discarded costs at most one spurious refresh, while a missed subscription is a stale panel
    until someone clicks it.
    """


@dataclass(slots=True)
class _ComponentRender:
    """One component's current pure render result and everything that read produced."""

    revision: int
    root: bool
    inherited_context: dict[ContextKey[Any], object]
    child_context: dict[ContextKey[Any], object]
    nodes: tuple[RenderNode, ...]
    assets: tuple[Asset, ...]
    document_key: str | None
    observation: Observation
    async_bindings: tuple[AsyncBinding, ...]


@dataclass(slots=True)
class _ExpandedSubtree:
    """A complete expansion below one stable component path, before parent namespacing."""

    component: Component
    inherited_context: dict[ContextKey[Any], object]
    nodes: tuple[LayoutNode, ...]
    components: dict[str, Component]
    assets: tuple[Asset, ...]
    async_bindings: tuple[AsyncBinding, ...]
    observations: tuple[Address, ...]


class Component[ModeT = Any](StateOwner):
    """Base class for mounted, stateful views."""

    _runtime: RuntimeOwner | None = None
    _parent: Component | None = None
    _loaded: bool = False
    """Whether this instance's :meth:`on_load` has completed. Owned by the frontend."""
    _reactive_internal_attributes = frozenset(
        {"_runtime", "_parent", "_loaded", "_state_revision", "_dependency_invalidation"}
    )
    _reactive_require_state = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        cls._reactive_require_state = cls.render is not Component.render
        super().__init_subclass__(**kwargs)

    def _state_changed(self, names: frozenset[str]) -> None:
        """React to committed writes to these state slots.

        Nothing is refreshed here. A computed and a resource each record what they read and
        re-check it when something asks for the value, so a commit only has to say the tree
        needs drawing again. `names` is for a subclass that wants to know which fields moved.
        """
        del names
        self.__dict__["_dependency_invalidation"] = True
        try:
            self.invalidate()
        finally:
            self.__dict__.pop("_dependency_invalidation", None)

    def on_state_rollback(self) -> None:
        self.__dict__["_state_revision"] = self.__dict__.get("_state_revision", 0) + 1

    def render(self) -> RenderResult[ModeT]:
        """Describe the message for the current state. Pure and synchronous."""
        raise NotImplementedError

    def boundary(self, child: Component, *, key: str) -> Boundary:
        """Place child in this render tree under a stable key and namespace."""
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
        """Run after this component first enters a successfully drawn tree."""

    def on_unmount(self) -> None:
        """Run after this component leaves a successfully drawn tree."""

    def invalidate(self) -> None:
        """Mark this component's message as needing a re-render."""
        dependency = self.__dict__.get("_dependency_invalidation", False)
        if not dependency:
            self.__dict__["_state_revision"] = self.__dict__.get("_state_revision", 0) + 1
        if self._runtime is not None:
            self._runtime.invalidate(self, check_dependencies=dependency)
        elif self._parent is not None:
            self._parent.invalidate()

    def provide[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Provide an ephemeral value to descendants rendered below this component."""
        context = _CURRENT_CONTEXT.get()
        if context is None:
            message = "provide() is only available while rendering"
            raise RuntimeError(message)
        context[key] = value

    def inject[ValueT](self, key: ContextKey[ValueT], default: ValueT | object = _MISSING) -> ValueT:
        """Read the nearest provided value while rendering."""
        context = _CURRENT_CONTEXT.get()
        if context is not None and key in context:
            return context[key]  # pyrefly: ignore[bad-return]
        if default is not _MISSING:
            return default  # pyrefly: ignore[bad-return]
        message = f"no value was provided for context key {key.name!r}"
        raise LookupError(message)


def render_component_tree(
    root: Component,
    *,
    runtime: RuntimeOwner | None = None,
    context: dict[ContextKey[Any], object] | None = None,
    defer: Callable[[Component], bool] | None = None,
    _render_cache: dict[Component, _ComponentRender] | None = None,
    _dirty: set[Component] | None = None,
    _forced: set[Component] | None = None,
    _force_all: bool = False,
    _subtree_cache: dict[str, _ExpandedSubtree] | None = None,
    _dirty_paths: set[str] | None = None,
) -> ComponentTree:
    """Render and expand a component tree, preserving keyed component identity.

    ``defer`` makes this a *discovery* render: an embedded component it selects is recorded in
    :attr:`ComponentTree.deferred` and not expanded, so its :meth:`Component.render` is not
    called. That is what lets a frontend run :meth:`Component.on_load` before a component
    renders for the first time. The resulting tree is incomplete by construction; see
    :attr:`ComponentTree.deferred`.
    """
    components: dict[str, Component] = {}
    assets: list[Asset] = []
    document_key: str | None = None
    identities: dict[int, str] = {}
    active: set[int] = set()
    deferred: list[Component] = []
    observed_addresses: list[Address] = []
    observed_bindings: list[AsyncBinding] = []
    render_cache = {} if _render_cache is None else _render_cache
    dirty = set() if _dirty is None else _dirty
    forced = set() if _forced is None else _forced
    subtree_cache = {} if _subtree_cache is None else _subtree_cache
    dirty_paths = set() if _dirty_paths is None else _dirty_paths

    def items(rendered: RenderResult, path: str) -> tuple[tuple[RenderNode, ...], tuple[Asset, ...], str | None]:
        if isinstance(rendered, Document):
            if rendered.key is not None and path != "$":
                message = f"{path}: only the root component may return a keyed Document"
                raise LayoutInvariantError(message)
            return rendered.children, rendered.assets, rendered.key
        nodes = tuple(rendered) if isinstance(rendered, Sequence) else (rendered,)
        return nodes, (), None

    def same_context(left: dict[ContextKey[Any], object], right: dict[ContextKey[Any], object]) -> bool:
        if left.keys() != right.keys():
            return False
        for key, value in left.items():
            other = right[key]
            if value is other:
                continue
            try:
                if value != other:
                    return False
            except Exception:
                return False
        return True

    def rendered(
        component: Component,
        path: str,
        inherited_context: dict[ContextKey[Any], object],
    ) -> _ComponentRender:
        cached = render_cache.get(component)
        revision = component.__dict__.get("_state_revision", 0)
        dependency_check = component in dirty and component not in forced
        reusable = (
            not _force_all
            and component not in forced
            and cached is not None
            and cached.revision == revision
            and cached.root == (path == "$")
            and same_context(cached.inherited_context, inherited_context)
            and (component not in dirty or (dependency_check and cached.observation.current()))
        )
        if reusable:
            observed_addresses.extend(cached.observation.addresses())
            observed_bindings.extend(cached.async_bindings)
            assets.extend(cached.assets)
            return cached

        local_context = dict(inherited_context)
        token = _CURRENT_CONTEXT.set(local_context)
        try:
            with observe_render() as observation, observe_async_bindings() as bindings:
                if active_observation := _RENDER_OBSERVATION.get():
                    active_observation.entering_own_render(component)
                value = component.render()
                own_nodes, own_assets, own_key = items(value, path)
        finally:
            _CURRENT_CONTEXT.reset(token)
        snapshot = _ComponentRender(
            revision,
            path == "$",
            dict(inherited_context),
            local_context,
            own_nodes,
            own_assets,
            own_key,
            observation,
            unique_async_bindings(bindings),
        )
        observed_addresses.extend(observation.addresses())
        observed_bindings.extend(snapshot.async_bindings)
        assets.extend(own_assets)
        if not any(isinstance(value, Component) for value in observation.constructed.values()):
            render_cache[component] = snapshot
        else:
            render_cache.pop(component, None)
        return snapshot

    def expand(
        component: Component,
        path: str,
        inherited_context: dict[ContextKey[Any], object],
    ) -> list[LayoutNode]:
        nonlocal document_key
        identity = id(component)
        if identity in active:
            message = f"{path}: component embedding cycle"
            raise LayoutInvariantError(message)
        if previous := identities.get(identity):
            message = f"{path}: component instance is already embedded at {previous}"
            raise LayoutInvariantError(message)
        cached_subtree = subtree_cache.get(path)
        if (
            not _force_all
            and path not in dirty_paths
            and cached_subtree is not None
            and cached_subtree.component is component
            and same_context(cached_subtree.inherited_context, inherited_context)
        ):
            for cached_path, cached_component in cached_subtree.components.items():
                cached_identity = id(cached_component)
                if previous := identities.get(cached_identity):
                    message = f"{cached_path}: component instance is already embedded at {previous}"
                    raise LayoutInvariantError(message)
                identities[cached_identity] = cached_path
                components[cached_path] = cached_component
                cached_component._runtime = runtime
            assets.extend(cached_subtree.assets)
            observed_bindings.extend(cached_subtree.async_bindings)
            observed_addresses.extend(cached_subtree.observations)
            return list(cached_subtree.nodes)

        component_paths_before = set(components)
        asset_start = len(assets)
        binding_start = len(observed_bindings)
        observation_start = len(observed_addresses)
        deferred_start = len(deferred)
        identities[identity] = path
        components[path] = component
        component._runtime = runtime
        active.add(identity)
        embed_keys: set[str] = set()
        snapshot = rendered(component, path, inherited_context)
        context = snapshot.child_context

        def expand_item(item: RenderNode, item_path: str) -> list[LayoutNode]:
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
                item.component._parent = component
                if defer is not None and defer(item.component):
                    deferred.append(item.component)
                    return []
                child_path = item.key if path == "$" else f"{path}.{item.key}"
                return _namespace(expand(item.component, child_path, context), item.key)
            return [map_layout_children(item, item_path, expand_item)]

        try:
            nodes: list[LayoutNode] = []
            for index, item in enumerate(snapshot.nodes):
                nodes.extend(expand_item(item, f"{path}.{index}"))
            if snapshot.document_key is not None:
                document_key = snapshot.document_key
            if len(deferred) == deferred_start:
                subtree_cache[path] = _ExpandedSubtree(
                    component,
                    dict(inherited_context),
                    tuple(nodes),
                    {
                        component_path: held
                        for component_path, held in components.items()
                        if component_path not in component_paths_before
                    },
                    tuple(assets[asset_start:]),
                    unique_async_bindings(observed_bindings[binding_start:]),
                    tuple(dict.fromkeys(observed_addresses[observation_start:])),
                )
            else:
                subtree_cache.pop(path, None)
            return nodes
        finally:
            active.remove(identity)

    try:
        nodes = tuple(expand(root, "$", context or {}))
    except _AtomicResourcePending as pending:
        # Atomic state is never rendered while pending. Keep the resource observation from
        # the aborted discovery pass so the frontend can settle it before retrying.
        observed_bindings.append(pending.resource)
        nodes = ()
    return ComponentTree(
        nodes,
        components,
        tuple(assets),
        document_key,
        tuple(deferred),
        unique_async_bindings(observed_bindings),
        tuple(dict.fromkeys(observed_addresses)),
    )


def _namespace(nodes: list[LayoutNode], prefix: str) -> list[LayoutNode]:
    """Rewrite an embedded subtree's control keys under ``prefix``.

    Explicit control keys are scoped under the embed path, so inserting a sibling cannot
    change the identity of any existing action.
    """

    def key_for(node: Button | SelectMenu) -> str:
        return f"{prefix}.{node.key}"

    def rewrite_text[T: (Text, Heading, Footer, Code, Lines)](node: T) -> T:
        overflow = node.overflow
        if isinstance(overflow, Paginate) and overflow.key is not None:
            return replace(node, overflow=replace(overflow, key=f"{prefix}.{overflow.key}"))
        return node

    def rewrite_item[T](item: T) -> T:
        return replace(item, key=key_for(item)) if isinstance(item, Button) else item  # pyrefly: ignore

    def rewrite_semantic_action(
        item: SemanticAction | SemanticLink | SemanticActionGroup,
    ) -> SemanticAction | SemanticLink | SemanticActionGroup:
        if isinstance(item, SemanticActionGroup):
            return replace(
                item,
                key=f"{prefix}.{item.key}",
                actions=tuple(rewrite_semantic_action(action) for action in item.actions),
            )
        return replace(item, key=f"{prefix}.{item.key}")

    def rewrite(node: LayoutNode) -> LayoutNode:
        match node:
            case SemanticActions(items=items, key=key):
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
            case ActionGroup(items=items):
                return ActionGroup(items=tuple(rewrite_item(item) for item in items))
            case Section(texts=texts, accessory=accessory):
                return Section(texts=tuple(rewrite_text(text) for text in texts), accessory=rewrite_item(accessory))
            case SelectMenu():
                return replace(node, key=key_for(node))
            case _:
                return map_layout_children(node, "$", lambda child, _path: (rewrite(child),))

    return [rewrite(node) for node in nodes]

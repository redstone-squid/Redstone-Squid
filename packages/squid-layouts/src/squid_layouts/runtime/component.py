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

from squid_layouts.document import Asset, Document
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.primitives.constraints import Paginate
from squid_layouts.primitives.nodes import (
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
from squid_layouts.runtime._tree import map_layout_children
from squid_layouts.runtime.context import ContextKey
from squid_layouts.runtime.resources import (
    AsyncBinding,
    _AtomicResourcePending,
    observe_async_bindings,
    unique_async_bindings,
)
from squid_layouts.runtime.topics import Address
from squid_layouts.semantic import (
    Action as SemanticAction,
)
from squid_layouts.semantic import (
    ActionGroup as SemanticActionGroup,
)
from squid_layouts.semantic import (
    Actions as SemanticActions,
)
from squid_layouts.semantic import (
    Choices as SemanticChoices,
)
from squid_layouts.semantic import (
    Details as SemanticDetails,
)
from squid_layouts.semantic import Download as SemanticDownload
from squid_layouts.semantic import (
    FormTrigger as SemanticFormTrigger,
)
from squid_layouts.semantic import (
    Items as SemanticItems,
)
from squid_layouts.semantic import (
    LayoutNode,
)
from squid_layouts.semantic import (
    Link as SemanticLink,
)
from squid_layouts.semantic import (
    List as SemanticList,
)
from squid_layouts.semantic import (
    Media as SemanticMedia,
)
from squid_layouts.semantic import (
    Navigation as SemanticNavigation,
)
from squid_layouts.semantic import Paged as SemanticPaged
from squid_layouts.semantic import Roster as SemanticRoster
from squid_layouts.semantic import (
    Table as SemanticTable,
)
from squid_layouts.semantic import Toggle as SemanticToggle
from squid_reactive.core import (
    _RENDER_OBSERVATION,
    Reactive,
    observe_render,
)

type RenderNode[ModeT = Any] = LayoutNode[ModeT]
type RenderResult[ModeT = Any] = Document[ModeT] | LayoutNode[ModeT] | Sequence[LayoutNode[ModeT]]


class RuntimeOwner(Protocol):
    def invalidate(self) -> None: ...


_CURRENT_CONTEXT: ContextVar[dict[ContextKey[Any], object] | None] = ContextVar(
    "squid_layouts_component_context", default=None
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


class Component[ModeT = Any](Reactive):
    """Base class for mounted, stateful views."""

    _runtime: RuntimeOwner | None = None
    _parent: Component | None = None
    _loaded: bool = False
    """Whether this instance's :meth:`on_load` has completed. Owned by the frontend."""
    _reactive_internal_attributes = frozenset({"_runtime", "_parent", "_loaded"})
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
        self.invalidate()

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
        self.__dict__["_state_revision"] = self.__dict__.get("_state_revision", 0) + 1
        if self._runtime is not None:
            self._runtime.invalidate()
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

    def items(rendered: RenderResult, path: str) -> tuple[RenderNode, ...]:
        nonlocal document_key
        if isinstance(rendered, Document):
            if rendered.key is not None:
                if path != "$":
                    message = f"{path}: only the root component may return a keyed Document"
                    raise LayoutInvariantError(message)
                document_key = rendered.key
            assets.extend(rendered.assets)
            return rendered.children
        return tuple(rendered) if isinstance(rendered, Sequence) else (rendered,)

    def expand(
        component: Component,
        path: str,
        inherited_context: dict[ContextKey[Any], object],
    ) -> list[LayoutNode]:
        identity = id(component)
        if identity in active:
            message = f"{path}: component embedding cycle"
            raise LayoutInvariantError(message)
        if previous := identities.get(identity):
            message = f"{path}: component instance is already embedded at {previous}"
            raise LayoutInvariantError(message)
        identities[identity] = path
        components[path] = component
        component._runtime = runtime
        active.add(identity)
        embed_keys: set[str] = set()
        context = dict(inherited_context)
        token = _CURRENT_CONTEXT.set(context)

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
            # Past this point, a write of this component's own state is no longer construction:
            # its own render() is the thing that could tear, so the exemption ends here and not
            # a moment later.
            if observation := _RENDER_OBSERVATION.get():
                observation.entering_own_render(component)
            nodes: list[LayoutNode] = []
            for index, item in enumerate(items(component.render(), path)):
                nodes.extend(expand_item(item, f"{path}.{index}"))
            return nodes
        finally:
            _CURRENT_CONTEXT.reset(token)
            active.remove(identity)

    with observe_render() as observation, observe_async_bindings() as observed:
        try:
            nodes = tuple(expand(root, "$", context or {}))
        except _AtomicResourcePending as pending:
            # Atomic state is never rendered while pending. Keep the resource observation from
            # the aborted discovery pass so the frontend can settle it before retrying.
            observed.append(pending.resource)
            nodes = ()
    return ComponentTree(
        nodes,
        components,
        tuple(assets),
        document_key,
        tuple(deferred),
        unique_async_bindings(observed),
        observation.addresses(),
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

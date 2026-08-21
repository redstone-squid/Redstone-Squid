"""Stateful components: render() is a pure function of state; mutating state re-renders.

A component describes *what the message should say now*. Interaction callbacks just mutate
declared state; assignments and in-place list, dict, or set mutations schedule the mount to
re-render. Components never touch discord.py objects directly.

Components compose through explicit keyed Embed boundaries, so two instances of the same
child class can appear in one message without their controls or pagers cross-wiring. Only the
root component is attached to a Mount; children reach it through their parent.
"""

import functools
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Protocol, Self

from squid_layouts.document import Asset, Document
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.primitives.constraints import Paginate
from squid_layouts.primitives.nodes import (
    ActionGroup,
    Button,
    Code,
    Embed,
    Extension,
    Footer,
    Heading,
    Lines,
    Node,
    Panel,
    Row,
    Section,
    SelectMenu,
    Text,
    Variant,
    Variants,
)
from squid_layouts.runtime.context import ContextKey
from squid_layouts.runtime.reactivity import _CURRENT, _State, report_undeclared_write
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
    Article as SemanticArticle,
)
from squid_layouts.semantic import (
    Aside as SemanticAside,
)
from squid_layouts.semantic import (
    BestEffort as SemanticBestEffort,
)
from squid_layouts.semantic import (
    Choices as SemanticChoices,
)
from squid_layouts.semantic import (
    Cluster as SemanticCluster,
)
from squid_layouts.semantic import (
    Details as SemanticDetails,
)
from squid_layouts.semantic import (
    FallbackContent as SemanticFallbackContent,
)
from squid_layouts.semantic import (
    Group as SemanticGroup,
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
from squid_layouts.semantic import (
    OptionalContent as SemanticOptionalContent,
)
from squid_layouts.semantic import (
    Section as SemanticSection,
)
from squid_layouts.semantic import (
    Spilled as SemanticSpilled,
)
from squid_layouts.semantic import (
    Stack as SemanticStack,
)
from squid_layouts.semantic import (
    Table as SemanticTable,
)
from squid_layouts.semantic import (
    Truncated as SemanticTruncated,
)

type RenderNode = LayoutNode
type RenderResult = Document | LayoutNode | Sequence[LayoutNode]


class RuntimeOwner(Protocol):
    def invalidate(self) -> None: ...


_CURRENT_CONTEXT: ContextVar[dict[ContextKey[Any], object] | None] = ContextVar(
    "squid_layouts_component_context", default=None
)
_MISSING = object()

# Written by the tree walker, not by authors, so they are never an author's state change.
_FRAMEWORK_ATTRIBUTES = frozenset({"_runtime", "_parent", "_loaded"})


def _is_abstract(cls: type) -> bool:
    """Whether this class is a base to build on rather than one to instantiate.

    Such a class may declare state only its concrete subclasses can assign, so its constructor
    is not the place to demand one. Not having implemented render is the test: `ABCMeta` needs
    no special case, both because it populates `__abstractmethods__` only after
    `__init_subclass__` has run, and because it already refuses to instantiate the class, so
    that wrapper can never be the outermost one.
    """
    return cls.render is Component.render


def _checked_init(
    original: Callable[..., None],
    required: tuple[tuple[str, str], ...],
) -> Callable[..., None]:
    """Wrap ``__init__`` so state declared without an initial value must be assigned."""

    @functools.wraps(original)
    def __init__(self: Component, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        # Only the outermost __init__ checks. A subclass calling super().__init__() would
        # otherwise trip the base's wrapper before it had finished assigning.
        if type(self).__init__ is not __init__:
            return
        missing = sorted(name for name, slot in required if slot not in self.__dict__)
        if missing:
            message = f"{type(self).__name__}.__init__ left declared state unassigned: {', '.join(missing)}"
            raise TypeError(message)

    return __init__


@dataclass(frozen=True, slots=True)
class ComponentTree:
    """One expanded render and the component identities that produced it."""

    nodes: tuple[LayoutNode, ...]
    components: dict[str, Component]
    assets: tuple[Asset, ...] = ()
    document_key: str | None = None
    deferred: tuple[Component, ...] = ()
    """Embedded components expansion stopped at, in the order it met them.

    Only ever non-empty for a discovery render (see ``render_component_tree``'s ``defer``).
    Such a tree is missing whole subtrees, so it describes what to load next and nothing else:
    never plan it, draw it, or commit it.
    """


class Component:
    """Base class for mounted, stateful views."""

    _runtime: RuntimeOwner | None = None
    _parent: Component | None = None
    _loaded: bool = False
    """Whether this instance's :meth:`on_load` has completed. Owned by the frontend."""
    _state_names: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = {
            name: descriptor
            for klass in reversed(cls.__mro__)
            for name, descriptor in vars(klass).items()
            if isinstance(descriptor, _State)
        }
        cls._state_names = frozenset(declared)
        required = tuple(
            (name, descriptor._name) for name, descriptor in declared.items() if not descriptor.has_initial
        )
        if required and not _is_abstract(cls):
            # Wrap even an inherited __init__, so adding a required field to a subclass that
            # defines no constructor of its own is still checked.
            cls.__init__ = _checked_init(cls.__init__, required)

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        # A handler may build components. Noting the ones born mid-action is what lets their
        # __init__ write freely while a live component's writes stay covered.
        instance = super().__new__(cls)
        if current := _CURRENT.get():
            current.note_born(instance)
        return instance

    def __setattr__(self, name: str, value: Any) -> None:
        # Fast path: one contextvar read when no action is in flight, which is almost always.
        if _CURRENT.get() is not None and name not in _FRAMEWORK_ATTRIBUTES and name not in type(self)._state_names:
            report_undeclared_write(self, name)
        object.__setattr__(self, name, value)

    def _state_changed(self) -> None:
        self.invalidate()

    def _state_rolled_back(self) -> None:
        self.__dict__["_state_revision"] = self.__dict__.get("_state_revision", 0) + 1

    def render(self) -> RenderResult:
        """Describe the message for the current state. Pure and synchronous."""
        raise NotImplementedError

    def embed(self, child: Component, *, key: str) -> Embed:
        """Place child in this render tree under a stable key and namespace."""
        return Embed(child, key)

    async def on_load(self) -> None:
        """Fetch what this component cannot render without, before its first render.

        Runs once per instance, before the first delivery that would show it, and before
        :meth:`render` is ever called on it: a frontend stops expanding at an unloaded
        component rather than rendering it empty. Writes are ordinary pre-delivery state, so
        the delivered view is the loaded one -- there is no loading paint to design for.

        A raise leaves the instance eligible to retry on the next delivery attempt, and
        nothing is delivered in the meantime. Data the component can degrade without belongs
        in declared state with a render branch, refreshed by a handler, not here.
        """

    def on_mount(self) -> None:
        """Run after this component first enters a successfully drawn tree."""

    def on_unmount(self) -> None:
        """Run after this component leaves a successfully drawn tree."""

    def mutated(self, name: str) -> None:
        """Re-render because declared state changed in place where nothing observed it.

        Assignment and list, dict, and set mutation are observed already. Reach for this only
        when a field's *contents* changed some other way, such as setting an attribute on the
        object a ``copy="ref"`` field holds. It schedules the draw; it cannot roll the change
        back, and naming the field keeps the call tied to the declaration it depends on.
        """
        if name not in type(self)._state_names:
            message = f"{type(self).__name__}.{name} is not declared state, so it cannot have changed in place"
            raise TypeError(message)
        self.invalidate()

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

    def one(expanded: list[LayoutNode], path: str) -> LayoutNode:
        if len(expanded) != 1:
            message = f"{path}: this structural position requires exactly one node"
            raise LayoutInvariantError(message)
        return expanded[0]

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
            if isinstance(item, Embed):
                if item.key in embed_keys:
                    message = f"{item_path}: duplicate Embed key {item.key!r}"
                    raise LayoutInvariantError(message)
                embed_keys.add(item.key)
                if not isinstance(item.component, Component):
                    message = f"{item_path}: Embed does not contain a Component"
                    raise LayoutInvariantError(message)
                # Before the defer check: a deferred child still reaches the mount through
                # its parent when its on_load writes state.
                item.component._parent = component
                if defer is not None and defer(item.component):
                    deferred.append(item.component)
                    return []
                child_path = item.key if path == "$" else f"{path}.{item.key}"
                return _namespace(expand(item.component, child_path, context), item.key)
            match item:
                case (
                    SemanticGroup(children=children)
                    | SemanticStack(children=children)
                    | SemanticCluster(children=children)
                    | SemanticSection(children=children)
                    | SemanticArticle(children=children)
                    | SemanticAside(children=children)
                    | SemanticDetails(children=children)
                ):
                    return [replace(item, children=expand_children(children, item_path))]
                case SemanticItems(items=items):
                    return [
                        replace(
                            item,
                            items=tuple(
                                replace(
                                    entry,
                                    children=expand_children(entry.children, f"{item_path}.item.{index}"),
                                )
                                for index, entry in enumerate(items)
                            ),
                        )
                    ]
                case (
                    SemanticTruncated(node=child)
                    | SemanticSpilled(node=child)
                    | SemanticOptionalContent(node=child)
                    | SemanticBestEffort(node=child)
                ):
                    node_path = f"{item_path}.node"
                    return [replace(item, node=one(expand_item(child, node_path), node_path))]
                case SemanticFallbackContent(primary=primary, alternates=alternates):
                    primary_path = f"{item_path}.primary"
                    return [
                        replace(
                            item,
                            primary=one(expand_item(primary, primary_path), primary_path),
                            alternates=tuple(
                                one(
                                    expand_item(alternate, f"{item_path}.alternate.{index}"),
                                    f"{item_path}.alternate.{index}",
                                )
                                for index, alternate in enumerate(alternates)
                            ),
                        )
                    ]
                case Panel(children=children, accent=accent):
                    expanded: list[Node] = []
                    for index, child in enumerate(children):
                        expanded.extend(expand_item(child, f"{item_path}.{index}"))  # pyrefly: ignore
                    return [Panel(tuple(expanded), accent)]
                case Variants(variants=variants, priority=priority):
                    rungs: list[Variant] = []
                    for index, variant in enumerate(variants):
                        expanded_rung: list[Node] = []
                        for child_index, child in enumerate(variant.nodes):
                            expanded_rung.extend(expand_item(child, f"{item_path}.variant.{index}.{child_index}"))  # pyrefly: ignore
                        rungs.append(Variant(tuple(expanded_rung), variant.requires))
                    return [Variants(tuple(rungs), priority)]
                case Extension(kind=kind, version=version, payload=payload, fallback=fallback):
                    expanded = expand_item(fallback, f"{item_path}.fallback")
                    return [Extension(kind, version, payload, one(expanded, f"{item_path}.fallback"))]
                case _:
                    return [item]

        def expand_children(children: Sequence[RenderNode], parent_path: str) -> tuple[LayoutNode, ...]:
            expanded: list[LayoutNode] = []
            for index, child in enumerate(children):
                expanded.extend(expand_item(child, f"{parent_path}.{index}"))
            return tuple(expanded)

        try:
            nodes: list[LayoutNode] = []
            for index, item in enumerate(items(component.render(), path)):
                nodes.extend(expand_item(item, f"{path}.{index}"))
            return nodes
        finally:
            _CURRENT_CONTEXT.reset(token)
            active.remove(identity)

    nodes = tuple(expand(root, "$", context or {}))
    return ComponentTree(nodes, components, tuple(assets), document_key, tuple(deferred))


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
            case (
                SemanticGroup(children=children) | SemanticStack(children=children) | SemanticCluster(children=children)
            ):
                return replace(node, children=tuple(rewrite(child) for child in children))
            case (
                SemanticSection(children=children)
                | SemanticArticle(children=children)
                | SemanticAside(children=children)
            ):
                return replace(node, children=tuple(rewrite(child) for child in children))
            case SemanticDetails(key=key, children=children):
                return replace(
                    node,
                    key=f"{prefix}.{key}",
                    children=tuple(rewrite(child) for child in children),
                )
            case SemanticItems(key=key, items=items):
                return replace(
                    node,
                    key=f"{prefix}.{key}",
                    items=tuple(
                        replace(item, children=tuple(rewrite(child) for child in item.children)) for item in items
                    ),
                )
            case (
                SemanticList(key=key)
                | SemanticChoices(key=key)
                | SemanticNavigation(key=key)
                | SemanticTable(key=key)
                | SemanticMedia(key=key)
            ):
                return replace(node, key=f"{prefix}.{key}")
            case (
                SemanticTruncated(node=child)
                | SemanticSpilled(node=child)
                | SemanticOptionalContent(node=child)
                | SemanticBestEffort(node=child)
            ):
                return replace(node, node=rewrite(child))
            case SemanticFallbackContent(primary=primary, alternates=alternates):
                return replace(
                    node,
                    primary=rewrite(primary),
                    alternates=tuple(rewrite(alternate) for alternate in alternates),
                )
            case Text() | Heading() | Footer() | Code() | Lines():
                return rewrite_text(node)
            case Panel(children=children, accent=accent):
                return Panel(children=tuple(rewrite(child) for child in children), accent=accent)
            case Row(items=items):
                return Row(items=tuple(rewrite_item(item) for item in items))
            case ActionGroup(items=items):
                return ActionGroup(items=tuple(rewrite_item(item) for item in items))
            case Section(texts=texts, accessory=accessory):
                return Section(texts=tuple(rewrite_text(text) for text in texts), accessory=rewrite_item(accessory))
            case SelectMenu():
                return replace(node, key=key_for(node))
            case Extension(kind=kind, version=version, payload=payload, fallback=fallback):
                return Extension(kind, version, payload, rewrite(fallback))
            case Variants(variants=variants, priority=priority):
                return Variants(
                    variants=tuple(
                        Variant(tuple(rewrite(child) for child in variant.nodes), variant.requires)
                        for variant in variants
                    ),
                    priority=priority,
                )
            case _:
                return node

    return [rewrite(node) for node in nodes]

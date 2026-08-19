"""Stateful components: render() is a pure function of state; mutating state re-renders.

A component describes *what the message should say now*. Interaction callbacks just mutate
declared state; assignments and in-place list, dict, or set mutations schedule the mount to
re-render. Components never touch discord.py objects directly.

Components compose through explicit keyed Embed boundaries, so two instances of the same
child class can appear in one message without their controls or pagers cross-wiring. Only the
root component is attached to a Mount; children reach it through their parent.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from squid_layouts.constraints import Paginate
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.ir import (
    ActionGroup,
    Button,
    Choice,
    Code,
    Embed,
    Extension,
    Fold,
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
)
from squid_layouts.semantic import LayoutNode

if TYPE_CHECKING:
    from squid_layouts.mount import Mount


type RenderNode = LayoutNode
type RenderResult = LayoutNode | Sequence[LayoutNode]


@dataclass(frozen=True, slots=True)
class ComponentTree:
    """One expanded render and the component identities that produced it."""

    nodes: tuple[LayoutNode, ...]
    components: dict[str, Component]


class Component:
    """Base class for mounted, stateful views."""

    _mount: Mount | None = None
    _parent: Component | None = None

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

    def on_mount(self) -> None:
        """Run after this component first enters a successfully drawn tree."""

    def on_unmount(self) -> None:
        """Run after this component leaves a successfully drawn tree."""

    def invalidate(self) -> None:
        """Mark this component's message as needing a re-render."""
        self.__dict__["_state_revision"] = self.__dict__.get("_state_revision", 0) + 1
        if self._mount is not None:
            self._mount.invalidate()
        elif self._parent is not None:
            self._parent.invalidate()

    @property
    def mount(self) -> Mount:
        """The mount this component's tree is attached to. Only valid after mounting."""
        component: Component = self
        while component._mount is None and component._parent is not None:
            component = component._parent
        if component._mount is None:
            message = "component is not mounted"
            raise RuntimeError(message)
        return component._mount


def render_component_tree(root: Component) -> ComponentTree:
    """Render and expand a component tree, preserving keyed component identity."""
    components: dict[str, Component] = {}
    identities: dict[int, str] = {}
    active: set[int] = set()

    def items(rendered: RenderResult) -> tuple[RenderNode, ...]:
        return tuple(rendered) if isinstance(rendered, Sequence) else (rendered,)

    def one(expanded: list[LayoutNode], path: str) -> LayoutNode:
        if len(expanded) != 1:
            message = f"{path}: this structural position requires exactly one node"
            raise LayoutInvariantError(message)
        return expanded[0]

    def expand(component: Component, path: str) -> list[LayoutNode]:
        identity = id(component)
        if identity in active:
            message = f"{path}: component embedding cycle"
            raise LayoutInvariantError(message)
        if previous := identities.get(identity):
            message = f"{path}: component instance is already embedded at {previous}"
            raise LayoutInvariantError(message)
        identities[identity] = path
        components[path] = component
        active.add(identity)
        embed_keys: set[str] = set()

        def expand_item(item: RenderNode, item_path: str) -> list[LayoutNode]:
            if isinstance(item, Embed):
                if item.key in embed_keys:
                    message = f"{item_path}: duplicate Embed key {item.key!r}"
                    raise LayoutInvariantError(message)
                embed_keys.add(item.key)
                if not isinstance(item.component, Component):
                    message = f"{item_path}: Embed does not contain a Component"
                    raise LayoutInvariantError(message)
                item.component._parent = component
                child_path = item.key if path == "$" else f"{path}.{item.key}"
                return _namespace(expand(item.component, child_path), item.key)
            match item:
                case Panel(children=children, accent=accent):
                    expanded: list[Node] = []
                    for index, child in enumerate(children):
                        expanded.extend(expand_item(child, f"{item_path}.{index}"))  # pyrefly: ignore
                    return [Panel(tuple(expanded), accent)]
                case Fold(primary=primary, fallback=fallback, priority=priority):
                    return [
                        Fold(
                            one(expand_item(primary, f"{item_path}.primary"), f"{item_path}.primary"),
                            one(expand_item(fallback, f"{item_path}.fallback"), f"{item_path}.fallback"),
                            priority,
                        )
                    ]
                case Choice(variants=variants, priority=priority):
                    return [
                        Choice(
                            tuple(
                                Variant(
                                    one(
                                        expand_item(variant.node, f"{item_path}.variant.{index}"),
                                        f"{item_path}.variant.{index}",
                                    ),
                                    variant.requires,
                                )
                                for index, variant in enumerate(variants)
                            ),
                            priority,
                        )
                    ]
                case Extension(kind=kind, version=version, payload=payload, fallback=fallback):
                    expanded = expand_item(fallback, f"{item_path}.fallback")
                    return [Extension(kind, version, payload, one(expanded, f"{item_path}.fallback"))]
                case _:
                    return [item]

        try:
            nodes: list[LayoutNode] = []
            for index, item in enumerate(items(component.render())):
                nodes.extend(expand_item(item, f"{path}.{index}"))
            return nodes
        finally:
            active.remove(identity)

    return ComponentTree(tuple(expand(root, "$")), components)


def _namespace(nodes: list[Node], prefix: str) -> list[Node]:
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

    def rewrite(node: Node) -> Node:
        match node:
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
            case Fold(primary=primary, fallback=fallback, priority=priority):
                return Fold(primary=rewrite(primary), fallback=rewrite(fallback), priority=priority)
            case Choice(variants=variants, priority=priority):
                return Choice(
                    variants=tuple(Variant(rewrite(variant.node), variant.requires) for variant in variants),
                    priority=priority,
                )
            case _:
                return node

    return [rewrite(node) for node in nodes]

"""Stateful components: render() is a pure function of state; mutating state re-renders.

A component describes *what the message should say now*. Interaction callbacks just mutate
state (or call :meth:`Component.invalidate` after in-place mutation); the mount re-renders and
edits the message. Components never touch discord.py objects directly.

Components compose: :meth:`Component.embed` renders a child into its parent's document under
a key prefix, so two instances of the same child class can appear in one message without
their controls cross-wiring. Only the root component is attached to a `Mount`; children reach
it through their parent, which is also how a child's state change re-renders the message.
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from squid_layouts.ir import (
    ActionGroup,
    Button,
    Choice,
    Extension,
    Fold,
    Node,
    Panel,
    Row,
    Section,
    SelectMenu,
    Variant,
    as_nodes,
)

if TYPE_CHECKING:
    from squid_layouts.mount import Mount


class _State:
    """A descriptor that marks the owning component dirty on assignment."""

    def __init__(self, default: Any) -> None:
        self._default = default
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"__state_{name}"

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return instance.__dict__.get(self._name, self._default)

    def __set__(self, instance: Any, value: Any) -> None:
        instance.__dict__[self._name] = value
        invalidate = getattr(instance, "invalidate", None)
        if invalidate is not None:
            invalidate()


def state(default: Any) -> Any:
    """Declare reactive component state: ``count: int = state(0)``.

    Assignment (``self.count += 1``) marks the component's message for re-render. In-place
    mutation of a mutable value bypasses assignment — call :meth:`Component.invalidate`
    after it. Typed as ``Any`` so the declared attribute type is what checkers see.
    """
    return _State(default)


class Component:
    """Base class for mounted, stateful views."""

    _mount: Mount | None = None
    _parent: Component | None = None

    def render(self) -> Sequence[Node] | Node:
        """Describe the message for the current state. Pure and synchronous."""
        raise NotImplementedError

    def embed(self, child: Component, *, key: str) -> list[Node]:
        """Render ``child`` into this component's document, namespaced under ``key``.

        Every control in the child's subtree gets ``key`` as a prefix, so two instances of
        one child class stay independently addressable, and a keyless control keeps a stable
        identity as long as its position within *its own* subtree does not move.
        """
        child._parent = self
        return _namespace(as_nodes(child.render()), key)

    def invalidate(self) -> None:
        """Mark this component's message as needing a re-render."""
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


def _namespace(nodes: list[Node], prefix: str) -> list[Node]:
    """Rewrite an embedded subtree's control keys under ``prefix``.

    Explicit control keys are scoped under the embed path, so inserting a sibling cannot
    change the identity of any existing action.
    """

    def key_for(node: Button | SelectMenu) -> str:
        return f"{prefix}.{node.key}"

    def rewrite_item[T](item: T) -> T:
        return replace(item, key=key_for(item)) if isinstance(item, Button) else item  # pyrefly: ignore

    def rewrite(node: Node) -> Node:
        match node:
            case Panel(children=children, accent=accent):
                return Panel(children=tuple(rewrite(child) for child in children), accent=accent)
            case Row(items=items):
                return Row(items=tuple(rewrite_item(item) for item in items))
            case ActionGroup(items=items):
                return ActionGroup(items=tuple(rewrite_item(item) for item in items))
            case Section(texts=texts, accessory=accessory):
                return Section(texts=texts, accessory=rewrite_item(accessory))
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

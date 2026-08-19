"""Stateful components: render() is a pure function of state; mutating state re-renders.

A component describes *what the message should say now*. Interaction callbacks just mutate
state (or call :meth:`Component.invalidate` after in-place mutation); the mount re-renders and
edits the message. Components never touch discord.py objects directly.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from squid_layouts.ir import Node

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

    def render(self) -> Sequence[Node] | Node:
        """Describe the message for the current state. Pure and synchronous."""
        raise NotImplementedError

    def invalidate(self) -> None:
        """Mark this component's message as needing a re-render."""
        if self._mount is not None:
            self._mount.invalidate()

    @property
    def mount(self) -> Mount:
        """The mount this component is attached to. Only valid after mounting."""
        if self._mount is None:
            message = "component is not mounted"
            raise RuntimeError(message)
        return self._mount

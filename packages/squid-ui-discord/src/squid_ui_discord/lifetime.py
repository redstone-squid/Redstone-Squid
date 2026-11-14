"""The ownership tree for installed scopes and live presentations.

A presentation has one parent. Session membership and screen results refer to this
same tree instead of owning additional cleanup graphs. Nodes remain attached until
shutdown callbacks settle, so closing a parent also joins children already closing.
"""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from squid_ui_discord.closure import CloseReason

if TYPE_CHECKING:
    from squid_ui_discord.facade import Scope
    from squid_ui_discord.message_root import AnyMessageRoot


class Lifetime:
    def __init__(self, *, root: AnyMessageRoot | None = None, scope: Scope[Any, Any] | None = None) -> None:
        self.root = root
        self.scope = scope
        self.parent: Lifetime | None = None
        self.children: dict[Lifetime, None] = {}
        self.closing = False

    @property
    def closed(self) -> bool:
        return self.root.finished if self.root is not None else self.closing

    def scope_owner(self) -> Scope[Any, Any] | None:
        node: Lifetime | None = self
        while node is not None:
            if node.scope is not None:
                return node.scope
            node = node.parent
        return None

    def adopt(self, child: Lifetime) -> None:
        """Assign ownership, allowing a scope-owned presentation to become a child."""
        self.check_adoption(child)
        if child.parent is self:
            return
        child.detach()
        child.parent = self
        self.children[child] = None

    def check_adoption(self, child: Lifetime) -> None:
        """Validate an ownership transfer before delivery or registration can have effects."""
        if self.closed or child.closed:
            message = "cannot attach to a closed lifetime"
            raise RuntimeError(message)
        if child.parent is self:
            return
        node: Lifetime | None = self
        while node is not None:
            if node is child:
                message = "presentation ownership cannot contain a cycle"
                raise ValueError(message)
            node = node.parent
        old = child.parent
        if old is not None:
            if old.root is not None:
                message = "presentation already has a different parent"
                raise ValueError(message)
            if child.scope_owner() is not None and child.scope_owner() is not self.scope_owner():
                message = "presentation cannot move between application scopes"
                raise ValueError(message)

    def detach(self) -> None:
        if self.parent is not None:
            self.parent.children.pop(self, None)
            self.parent = None

    def roots(self) -> Iterator[AnyMessageRoot]:
        for child in tuple(self.children):
            if child.root is not None:
                yield child.root
            yield from child.roots()

    async def close_children(self, *, reason: CloseReason, disable: bool = True) -> None:
        """Accept every child's closure before joining cleanup, retaining all failures."""
        children = tuple(self.children)
        errors: list[Exception] = []
        for child in children:
            try:
                if child.root is not None:
                    await child.root.finish(disable=disable, wait=False, reason=reason)
                elif child.scope is not None:
                    await child.scope.close(reason=reason)
                else:
                    child.closing = True
                    await child.close_children(reason=reason, disable=disable)
            except Exception as error:
                errors.append(error)
        for child in children:
            if child.root is not None and child.root.finished:
                try:
                    await child.root.wait()
                except Exception as error:
                    errors.append(error)
        if errors:
            message = "owned presentation shutdown failed"
            raise ExceptionGroup(message, errors)

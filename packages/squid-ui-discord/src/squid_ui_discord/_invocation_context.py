"""Import-cycle-free ambient storage for invocation dispatch scopes."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid_ui_discord.invocation import Invocation


class InvocationCell:
    """A lazy invocation value shared by one ambient handler scope."""

    def __init__(self, source: object) -> None:
        self.source = source
        self.value: Invocation | None = None
        self.lock = asyncio.Lock()


_CURRENT_INVOCATION = ContextVar[InvocationCell | None]("squid_ui_discord_invocation", default=None)


def current_cell() -> InvocationCell | None:
    """Return the ambient lazy cell, when dispatch established one."""
    return _CURRENT_INVOCATION.get()


@contextmanager
def invocation_scope(source: object) -> Iterator[None]:
    """Establish a lazy invocation memo for the duration of one handler dispatch."""
    token = _CURRENT_INVOCATION.set(InvocationCell(source))
    try:
        yield
    finally:
        _CURRENT_INVOCATION.reset(token)


__all__ = ["current_cell", "invocation_scope"]

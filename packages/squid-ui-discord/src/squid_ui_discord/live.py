"""Weak references to message roots that have committed a visible generation.

The registry lets diagnostics list visible roots, including those opened without a keyed
session. It keeps only weak references, so the owning scope or host controls their lifetime.
Finish hooks remove roots when cleanup completes. Persistence across process restarts
belongs to :mod:`squid_ui_discord.durability`.
"""

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid_ui_discord.message_root import AnyMessageRoot

_LIVE: weakref.WeakValueDictionary[str, AnyMessageRoot] = weakref.WeakValueDictionary()


def track(message_root: AnyMessageRoot) -> None:
    """Record `message root` as live until it finishes.

    Idempotent, and meant to be called on every commit: the first call registers the
    deregistration hook, every later one is a dict lookup.
    """
    if _LIVE.get(message_root.id) is message_root:
        return
    _LIVE[message_root.id] = message_root
    # Exact deregistration rather than waiting for the collector: a finished message root is still
    # referenced by whatever host object opened it, and listing it as live would be a lie.
    message_root.on_finish(_forget)


async def _forget(message_root: AnyMessageRoot) -> None:
    if _LIVE.get(message_root.id) is message_root:
        del _LIVE[message_root.id]


def message_roots() -> tuple[AnyMessageRoot, ...]:
    """Every message root live in this process, in the order they first rendered."""
    return tuple(_LIVE.values())


def find(message_root_id: str) -> AnyMessageRoot | None:
    """The live message root with this id, or `None` if it has finished or was collected."""
    return _LIVE.get(message_root_id)

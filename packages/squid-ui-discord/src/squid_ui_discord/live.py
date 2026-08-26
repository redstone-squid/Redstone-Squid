"""Which mounts are live right now, so diagnostics can run while the bot does.

Planning has reports, fingerprints and metrics, and every one of them describes a render that
has already happened. None of them answer "show me every live UI session and why this one is
odd". This registry does, and deliberately owns nothing: entries are weak, a mount appears
when it first commits a render, and it leaves when it finishes. Nothing here keeps a mount
alive, starts a task, or sits on a hot path.

Distinct from :mod:`squid_ui_discord.durability`, which persists mounts across restarts,
and from a host's own session registry, which by design holds only the mounts it keyed.
"""

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid_ui_discord.message_root import MessageRoot

_LIVE: weakref.WeakValueDictionary[str, MessageRoot] = weakref.WeakValueDictionary()


def track(message_root: MessageRoot) -> None:
    """Record `mount` as live until it finishes.

    Idempotent, and meant to be called on every commit: the first call registers the
    deregistration hook, every later one is a dict lookup.
    """
    if _LIVE.get(message_root.id) is message_root:
        return
    _LIVE[message_root.id] = message_root
    # Exact deregistration rather than waiting for the collector: a finished mount is still
    # referenced by whatever host object opened it, and listing it as live would be a lie.
    message_root.on_finish(_forget)


async def _forget(message_root: MessageRoot) -> None:
    if _LIVE.get(message_root.id) is message_root:
        del _LIVE[message_root.id]


def message_roots() -> tuple[MessageRoot, ...]:
    """Every mount live in this process, in the order they first rendered."""
    return tuple(_LIVE.values())


def find(message_root_id: str) -> MessageRoot | None:
    """The live mount with this id, or `None` if it has finished or was collected."""
    return _LIVE.get(message_root_id)

"""One logical UI session per key, and what happens when a second one opens.

`Mount.lock_to` answers "who may click this"; nothing answered "how many of these may
exist". This registry owns that operational policy: a key names the session, `WhenOpen`
says what a second one does to the first, and `parent=` ties a mount spawned mid-handler to
the mount that spawned it so closing the parent does not leave a clickable orphan behind.

The layout core stays presentational; `squid_layouts.discord` is the operations layer. See
`docs/plans/squid-layouts-redesign/24-session-registry-move.md` for that placement decision.
Rejection wording remains with the host call site, so the registry stays testable against
stub mounts and reusable across applications.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Hashable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum, auto

import discord

from squid_layouts.discord.delivery import DeliveryReceipt, Destination
from squid_layouts.discord.mount import Mount

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Shipped convention for identifying one logical UI session.

    `scope` is whatever the session is per: a guild for a settings panel or a resource for
    an edit session. `None` means the session is user-global. Registry callers may use any
    hashable key instead when their application has different scoping needs.
    """

    name: str
    user_id: int
    scope: int | None = None


class WhenOpen(Enum):
    """What opening a session does to the one already under its key."""

    REPLACE = auto()
    """Finish the incumbent and open the new one."""

    REJECT = auto()
    """Leave the incumbent alone and deliver nothing."""


@dataclass(slots=True)
class _Entry:
    mount: Mount
    key: Hashable | None


class MountRegistry:
    """The live UI sessions this process owns, by key and by parent."""

    def __init__(self) -> None:
        self._by_key: dict[Hashable, _Entry] = {}
        self._children: dict[Mount, list[_Entry]] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._waiting: dict[Hashable, int] = {}

    async def open(
        self,
        mount: Mount,
        destination: Destination,
        *,
        key: Hashable | None = None,
        policy: WhenOpen = WhenOpen.REPLACE,
        parent: Mount | None = None,
    ) -> Mount | None:
        """Send `mount` through `destination` and register it as a session.

        Returns:
            The mount once it is live, or `None` when nothing was delivered. A `REJECT`
            policy declines to open a second session, while a destination can independently
            abandon delivery. Callers that need to distinguish those cases can check
            `get(key)` before opening.

        `policy` applies only when `key` is given. A keyless open still registers for parent
        cascade and still sends; it simply has no instance limit.
        """
        if key is None:
            return await self._deliver(mount, destination, key=None, parent=parent)
        # Held across the send and the incumbent's finish: two invocations in flight would
        # otherwise both see no incumbent and both survive.
        async with self._lock_for(key):
            incumbent = self._by_key.get(key)
            if incumbent is not None and incumbent.mount.finished:
                # Defence in depth. `on_finish` clears entries from every terminal path, so
                # this should be unreachable -- but a stale entry under REJECT would lock a
                # caller out of the session for the process's lifetime.
                logger.warning("session %s held a finished mount; discarding it", key)
                self._forget(incumbent)
                incumbent = None
            if incumbent is not None and policy is WhenOpen.REJECT:
                return None
            opened = await self._deliver(mount, destination, key=key, parent=parent)
            if opened is not None and incumbent is not None:
                # Only now: a failed or abandoned send must leave the incumbent standing
                # rather than costing the caller both panels.
                await incumbent.mount.finish()
            return opened

    def get(self, key: Hashable) -> Mount | None:
        """Return the mount currently holding `key`, if any."""
        entry = self._by_key.get(key)
        return None if entry is None else entry.mount

    async def close(self, key: Hashable, *, disable: bool = True) -> None:
        """Finish the session under `key`, if one is open."""
        entry = self._by_key.get(key)
        if entry is not None:
            await entry.mount.finish(disable=disable)

    async def close_all(self, *, disable: bool = True) -> None:
        """Finish every session this registry knows about.

        One unreachable message must not leave the rest of them live: this commonly runs at
        shutdown, where the alternative to a disabled panel is one that stays clickable and
        answers nothing.
        """
        for _, mount in list(self.active()):
            try:
                await mount.finish(disable=disable)
            except Exception:
                logger.exception("could not close mount %s", mount.id)

    def active(self) -> Iterator[tuple[Hashable | None, Mount]]:
        """Yield every live session, keyed ones first and each mount at most once."""
        seen: set[Mount] = set()
        for entry in list(self._by_key.values()):
            seen.add(entry.mount)
            yield entry.key, entry.mount
        for children in list(self._children.values()):
            for child in list(children):
                if child.mount not in seen:
                    seen.add(child.mount)
                    yield child.key, child.mount

    async def _deliver(
        self,
        mount: Mount,
        destination: Destination,
        *,
        key: Hashable | None,
        parent: Mount | None,
    ) -> Mount | None:
        """Send, and register only what actually reached Discord.

        `Mount.send` returns `None` for two different outcomes -- delivered without a handle,
        and abandoned without delivering -- so the flag reads the one that matters. A
        destination that abandons raises `DeliveryAbandoned`, which the mount swallows on its
        way to returning `None`; setting the flag after the await is what separates them. A
        destination that fails outright propagates and leaves the mount re-sendable.
        """
        delivered = False

        async def watched(view: discord.ui.LayoutView, files: list[discord.File]) -> DeliveryReceipt:
            nonlocal delivered
            receipt = await destination(view, files)
            delivered = True
            return receipt

        await mount.send(watched)
        if not delivered:
            logger.debug("session %s was not delivered; nothing registered", key)
            return None

        entry = _Entry(mount=mount, key=key)
        if key is not None:
            self._by_key[key] = entry

        async def release(finished: Mount) -> None:
            self._forget(entry)

        mount.on_finish(release)
        if parent is not None:
            await self._attach(parent, entry)
        return mount

    def _forget(self, entry: _Entry) -> None:
        """Drop `entry`'s key, if it still holds it.

        Identity-checked rather than deleted by key: `REPLACE` registers the newcomer before
        awaiting the incumbent's finish, so the incumbent's own hook fires against a key that
        already belongs to someone else.
        """
        if entry.key is None:
            return
        if self._by_key.get(entry.key) is entry:
            del self._by_key[entry.key]

    async def _attach(self, parent: Mount, child: _Entry) -> None:
        """Tie `child`'s lifetime to `parent`.

        `parent` need not be registered itself: a panel not using the registry is still a
        perfectly good parent, and the hook is all the cascade needs.
        """
        if parent.finished:
            # Nothing is left to wait for. A hook registered now would never fire, so the
            # child would outlive a parent that is already gone.
            await child.mount.finish()
            return
        children = self._children.get(parent)
        if children is None:
            children = self._children[parent] = []
            parent.on_finish(self._cascade)
        children.append(child)

    async def _cascade(self, parent: Mount) -> None:
        """Finish everything opened from inside `parent`, depth-first.

        Grandchildren follow by induction: each child's own finish fires its own cascade, and
        `Mount`'s finished guard terminates the recursion.
        """
        for child in self._children.pop(parent, ()):
            try:
                await child.mount.finish()
            except Exception:
                # One unreachable message must not strand its siblings.
                logger.exception("could not finish a child mount of %s", parent.id)

    @asynccontextmanager
    async def _lock_for(self, key: Hashable) -> AsyncIterator[None]:
        """Serialize opens on one key, keeping no lock for a key nobody is using."""
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._waiting[key] - 1
            if remaining:
                self._waiting[key] = remaining
            else:
                del self._waiting[key]
                del self._locks[key]

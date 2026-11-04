"""Process-local bookkeeping for context-reentrant build leases.

The tracker records which execution context currently holds a build's
in-process lease and how many times it has re-entered it. Leases are keyed on
a ContextVar rather than on the running task, because a child task inherits
its parent's context at spawn: keying on task identity meant a gather branch
or task-group child could not re-enter a lease its own caller held, and would
instead spin the acquire backoff against itself. BuildLockService combines
that bookkeeping with the persisted lock flag.
"""

import time
from collections.abc import AsyncGenerator, Collection, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

import anyio
from whenever import Instant

from squid.builds.errors import BuildBusyError
from squid.core.errors import InvalidStateError
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(slots=True)
class _Lease:
    """One held lease, shared by reference with every context that inherits it.

    The depth is mutated in place rather than rebound in the ContextVar so that
    a child task's nested acquire and release stay balanced against the same
    counter its parent is using.
    """

    token: UUID
    depth: int


_held_leases: ContextVar[Mapping[int, _Lease] | None] = ContextVar("squid.build_leases", default=None)


def _held() -> Mapping[int, _Lease]:
    """Return the leases held by the calling context."""
    return _held_leases.get() or {}


class BuildLockTracker:
    """Track context ownership of in-process build leases."""

    def __init__(self) -> None:
        self._lock_owners: dict[int, _Lease] = {}

    def is_held_locally(self, build_id: int) -> bool:
        """Return whether any context currently holds a local lease for *build_id*."""
        return build_id in self._lock_owners

    def _current_lease(self, build_id: int) -> _Lease | None:
        """Return the live lease for *build_id* held by the calling context.

        An inherited mapping can outlive the lease it names, so the entry only
        counts when it is still the lease this tracker considers held.
        """
        lease = _held().get(build_id)
        if lease is None or self._lock_owners.get(build_id) is not lease:
            return None
        return lease

    def try_reenter(self, build_id: int) -> bool:
        """Bump the lease count if the calling context already holds *build_id*'s lease."""
        lease = self._current_lease(build_id)
        if lease is None:
            return False
        lease.depth += 1
        return True

    def record_acquired(self, build_id: int, token: UUID) -> None:
        """Record that the calling context has newly acquired *build_id*'s lease."""
        lease = _Lease(token=token, depth=1)
        self._lock_owners[build_id] = lease
        _held_leases.set({**_held(), build_id: lease})

    def release(self, build_id: int) -> UUID | None:
        """Release one level of *build_id*'s lease for the calling context.

        Returns:
            The persisted lease token on the outermost release, or ``None`` if
            a nested lease remains or there was nothing to release.

        Raises:
            InvalidStateError: If a context that does not hold the lease tries
                to release it.
        """
        lease = self._current_lease(build_id)
        if lease is None:
            if build_id in self._lock_owners:
                msg = f"Build {build_id} lock can only be released by a context holding it."
                raise InvalidStateError(msg, context={"build_id": build_id})
            return None
        lease.depth -= 1
        if lease.depth > 0:
            return None
        self._lock_owners.pop(build_id, None)
        _held_leases.set({key: value for key, value in _held().items() if key != build_id})
        return lease.token

    def forget(self, build_ids: Collection[int]) -> None:
        """Drop process-local leases whose persisted lock has been reclaimed."""
        for build_id in build_ids:
            self._lock_owners.pop(build_id, None)

    def clear(self) -> None:
        """Forget all locally-tracked leases."""
        self._lock_owners.clear()
        _held_leases.set(None)


class BuildLockRepository(Protocol):
    async def acquire(self, build_id: int, token: UUID) -> bool: ...

    async def release(self, build_id: int, token: UUID) -> None: ...

    async def clean_stale(self) -> Collection[int]: ...


class BuildLockUnitOfWork(UnitOfWork, Protocol):
    @property
    def build_locks(self) -> BuildLockRepository: ...


class BuildLockService:
    """Coordinate reentrant leases using short acquisition and release transactions."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[BuildLockUnitOfWork]) -> None:
        self._uow_factory = uow_factory
        self._tracker = BuildLockTracker()

    async def acquire(self, build_id: int, *, blocking: bool = True, timeout: float = -1) -> bool:
        if self._tracker.try_reenter(build_id):
            return True
        if not blocking:
            return await self._try_acquire(build_id)
        delay = 0.01
        started_at = time.monotonic()
        while True:
            if await self._try_acquire(build_id):
                return True
            if timeout >= 0 and time.monotonic() - started_at >= timeout:
                return False
            await anyio.sleep(delay)
            delay = min(delay * 1.5, 0.5)

    async def _try_acquire(self, build_id: int) -> bool:
        if self._tracker.is_held_locally(build_id):
            return False
        token = uuid4()
        async with self._uow_factory() as uow:
            acquired = await uow.build_locks.acquire(build_id, token)
            await uow.commit()
        if acquired:
            self._tracker.record_acquired(build_id, token)
        return acquired

    async def release(self, build_id: int) -> None:
        token = self._tracker.release(build_id)
        if token is not None:
            async with self._uow_factory() as uow:
                await uow.build_locks.release(build_id, token)
                await uow.commit()

    @asynccontextmanager
    async def locked(self, build_id: int, *, timeout: float = 30) -> AsyncGenerator[None]:
        if not await self.acquire(build_id, timeout=timeout):
            raise BuildBusyError(build_id)
        try:
            yield
        finally:
            with anyio.CancelScope(shield=True):
                await self.release(build_id)

    async def clean_stale(self, *, older_than: Instant) -> None:
        """Reclaim leases whose persisted expiry has passed."""
        del older_than
        async with self._uow_factory() as uow:
            reclaimed = await uow.build_locks.clean_stale()
            await uow.commit()
        self._tracker.forget(reclaimed)

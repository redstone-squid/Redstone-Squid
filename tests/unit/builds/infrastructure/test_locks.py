"""Process-local build lease ownership tests."""

import contextvars
from uuid import UUID

import pytest

from squid.builds.infrastructure.locks import BuildLockTracker
from squid.core.errors import InvalidStateError

TOKEN = UUID("12345678-1234-5678-1234-567812345678")


def test_build_lock_is_reentrant_for_the_holding_context() -> None:
    tracker = BuildLockTracker()
    tracker.record_acquired(42, TOKEN)

    assert tracker.try_reenter(42)
    assert tracker.release(42) is None
    assert tracker.release(42) == TOKEN
    assert not tracker.is_held_locally(42)


def test_inherited_context_reenters_the_lease_its_caller_holds() -> None:
    tracker = BuildLockTracker()
    tracker.record_acquired(42, TOKEN)
    inherited = contextvars.copy_context()

    assert inherited.run(tracker.try_reenter, 42)
    assert inherited.run(tracker.release, 42) is None
    assert tracker.release(42) == TOKEN


def test_foreign_context_cannot_acquire_a_held_lease() -> None:
    tracker = BuildLockTracker()
    tracker.record_acquired(42, TOKEN)

    assert not contextvars.Context().run(tracker.try_reenter, 42)
    assert tracker.release(42) == TOKEN


def test_build_lock_rejects_release_from_a_foreign_context() -> None:
    tracker = BuildLockTracker()
    tracker.record_acquired(42, TOKEN)

    with pytest.raises(InvalidStateError, match="context holding it"):
        contextvars.Context().run(tracker.release, 42)

    assert tracker.release(42) == TOKEN


def test_reclaiming_a_persisted_lock_invalidates_inherited_local_authority() -> None:
    tracker = BuildLockTracker()
    tracker.record_acquired(42, TOKEN)
    inherited = contextvars.copy_context()

    tracker.forget([42])

    assert not tracker.is_held_locally(42)
    assert not inherited.run(tracker.try_reenter, 42)

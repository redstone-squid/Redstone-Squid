"""Portable guard admission: the built-in vocabulary and its ledger."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

import squid_layouts as sl
from squid_layouts import guards
from squid_layouts.guards import GuardLedger, GuardScope


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _event(actor: str = "7") -> sl.PressEvent:
    return sl.PressEvent(sl.Actor(actor), cast(sl.ActionResponder, _Responder()))


class _Responder:
    """A responder no guard test reaches; guards answer verdicts, not interactions."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"a guard touched the responder ({name})")


def _ledger(clock: _Clock, action: str = "press") -> GuardLedger:
    return GuardLedger(now=clock).for_action(action)


async def test_cooldown_admits_once_per_window_and_reports_the_wait() -> None:
    clock = _Clock()
    ledger = _ledger(clock)
    guard = guards.cooldown(30)

    assert (await guard.admit(_event(), ledger)).allowed
    denied = await guard.admit(_event(), ledger)
    assert not denied.allowed
    assert denied.retry_after == pytest.approx(30)
    assert denied.reason is None

    clock.advance(29)
    assert (await guard.admit(_event(), ledger)).retry_after == pytest.approx(1)
    clock.advance(1)
    assert (await guard.admit(_event(), ledger)).allowed


async def test_cooldown_counts_per_actor_unless_scoped_to_the_mount() -> None:
    clock = _Clock()
    ledger = _ledger(clock)
    per_actor = guards.cooldown(30)
    assert (await per_actor.admit(_event("1"), ledger)).allowed
    assert (await per_actor.admit(_event("2"), ledger)).allowed

    mount_wide = guards.cooldown(30, per=GuardScope.MOUNT, key="shared")
    assert (await mount_wide.admit(_event("1"), ledger)).allowed
    assert not (await mount_wide.admit(_event("2"), ledger)).allowed


async def test_a_shared_key_puts_two_actions_in_one_bucket() -> None:
    clock = _Clock()
    store = GuardLedger(now=clock)
    guard = guards.cooldown(30, key="votes")

    assert (await guard.admit(_event(), store.for_action("up"))).allowed
    assert not (await guard.admit(_event(), store.for_action("down"))).allowed

    unshared = guards.cooldown(30)
    assert (await unshared.admit(_event(), store.for_action("up"))).allowed
    assert (await unshared.admit(_event(), store.for_action("down"))).allowed


async def test_once_is_spent_per_actor_and_survives_the_whole_mount() -> None:
    clock = _Clock()
    ledger = _ledger(clock)
    guard = guards.once()

    assert (await guard.admit(_event("1"), ledger)).allowed
    assert not (await guard.admit(_event("1"), ledger)).allowed
    assert (await guard.admit(_event("2"), ledger)).allowed

    clock.advance(100_000)
    assert not (await guard.admit(_event("1"), ledger)).allowed


async def test_rate_limit_admits_a_burst_then_reports_the_oldest_press_expiring() -> None:
    clock = _Clock()
    ledger = _ledger(clock)
    guard = guards.rate_limit(2, 60)

    assert (await guard.admit(_event(), ledger)).allowed
    clock.advance(10)
    assert (await guard.admit(_event(), ledger)).allowed
    denied = await guard.admit(_event(), ledger)
    assert not denied.allowed
    assert denied.retry_after == pytest.approx(50)

    clock.advance(50)
    assert (await guard.admit(_event(), ledger)).allowed


def test_rate_limit_rejects_a_count_that_admits_nothing() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        guards.rate_limit(0, 60)


async def test_when_takes_synchronous_and_awaitable_predicates() -> None:
    ledger = _ledger(_Clock())
    allowed = 0

    async def eventually(event: sl.ActionEvent) -> bool:
        del event
        return allowed > 0

    synchronous = guards.when(lambda event: event.actor.id == "7", reason="Not you.")
    assert (await synchronous.admit(_event("7"), ledger)).allowed
    assert (await synchronous.admit(_event("8"), ledger)).reason == "Not you."

    asynchronous = guards.when(eventually, reason="Not yet.")
    assert not (await asynchronous.admit(_event(), ledger)).allowed
    allowed = 1
    assert (await asynchronous.admit(_event(), ledger)).allowed


async def test_permission_defaults_to_chromeless_denial() -> None:
    ledger = _ledger(_Clock())

    async def never(event: sl.ActionEvent) -> bool:
        del event
        return False

    verdict = await guards.permission(never).admit(_event(), ledger)
    assert not verdict.allowed
    assert verdict.reason is None
    assert (await guards.permission(never, reason="Mods only.").admit(_event(), ledger)).reason == "Mods only."


async def test_until_reads_the_wall_clock_and_refuses_a_naive_deadline() -> None:
    ledger = _ledger(_Clock())
    open_guard = guards.until(datetime.now(UTC) + timedelta(hours=1))
    closed = guards.until(datetime.now(UTC) - timedelta(seconds=1), reason="Voting closed.")

    assert (await open_guard.admit(_event(), ledger)).allowed
    verdict = await closed.admit(_event(), ledger)
    assert not verdict.allowed
    assert verdict.reason == "Voting closed."
    assert verdict.retry_after is None

    with pytest.raises(ValueError, match="aware deadline"):
        guards.until(datetime(2030, 1, 1))  # noqa: DTZ001


async def test_all_of_reports_the_first_denial_and_any_of_the_last() -> None:
    ledger = _ledger(_Clock())
    yes = guards.when(lambda event: True, reason="unused")
    no_first = guards.when(lambda event: False, reason="first")
    no_second = guards.when(lambda event: False, reason="second")

    assert (await guards.all_of(yes, yes).admit(_event(), ledger)).allowed
    assert (await guards.all_of(yes, no_first, no_second).admit(_event(), ledger)).reason == "first"

    assert (await guards.any_of(no_first, yes).admit(_event(), ledger)).allowed
    assert (await guards.any_of(no_first, no_second).admit(_event(), ledger)).reason == "second"


async def test_a_ledger_view_shares_entries_with_the_mount_wide_store() -> None:
    clock = _Clock()
    store = GuardLedger(now=clock)
    guard = guards.once()

    assert (await guard.admit(_event(), store.for_action("press"))).allowed
    assert not (await guard.admit(_event(), store.for_action("press"))).allowed

    store.clear()
    assert (await guard.admit(_event(), store.for_action("press"))).allowed

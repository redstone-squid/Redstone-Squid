"""Shared contracts for memory, SQLite, and PostgreSQL durable sessions."""

from pathlib import Path

import anyio
import pytest

from squid_storage import ClaimToken, SQLiteSessionStore

from .support import StorageBackend


async def test_fenced_session_store_contract(backend: StorageBackend) -> None:
    store = backend.sessions
    assert await store.load("missing") is None
    assert await store.claim("missing", "alpha", 10) is None
    first = await backend.publish("first", owner="alpha")
    second = await backend.publish("second", owner="alpha")
    assert tuple(record.key for record in await store.list()) == ("first", "second")
    assert await store.release(first)
    assert await store.release(second)

    alpha = await store.claim("first", "alpha", 10)
    assert alpha is not None
    assert await store.claim("first", "beta", 10) is None
    await backend.advance(10.1)
    beta = await store.claim("first", "beta", 10)
    assert beta is not None
    assert not await store.renew(alpha, 10)
    assert not await store.save(alpha, "stale", "stale")
    assert not await store.delete(alpha)
    assert not await store.release(alpha)
    assert await store.save(beta, "replacement snapshot", "replacement record")
    loaded = await store.load("first")
    assert loaded is not None
    assert loaded.snapshot_payload == "replacement snapshot"
    assert loaded.record_payload == "replacement record"
    assert await store.delete(beta)
    assert await store.load("first") is None


async def test_admission_atomically_retires_victims_and_fences_their_writers(backend: StorageBackend) -> None:
    victim = await backend.publish("victim")
    newcomer = await backend.publish("newcomer", victims=("victim",))
    assert await backend.sessions.load("victim") is None
    assert not await backend.sessions.save(victim, "stale", "stale")
    assert await backend.sessions.load("newcomer") is not None
    assert await backend.sessions.renew(newcomer, 10)


async def test_lost_admission_token_cannot_publish(backend: StorageBackend) -> None:
    store = backend.sessions
    stale = await store.reserve("scope", "first", 10)
    assert stale is not None
    await backend.advance(10.1)
    current = await store.reserve("scope", "second", 10)
    assert current is not None
    assert (
        await store.commit(
            stale, key="stale", snapshot_payload="snapshot", record_payload="record", victims=(), lease_seconds=10
        )
        is None
    )
    assert not await store.abandon(stale)
    assert await store.inspect(stale) is None
    assert await store.inspect(current) == ()


async def test_reclaiming_as_same_owner_invalidates_the_previous_token(backend: StorageBackend) -> None:
    store = backend.sessions
    old = await backend.publish("session")
    current = await store.claim("session", "writer", 10)
    assert current is not None
    assert not await store.release(old)
    assert not await store.save(old, "stale", "stale")
    assert await store.renew(current, 20)
    await backend.advance(10.1)
    assert await store.save(current, "live", "live")
    await backend.advance(10)
    assert not await store.renew(current, 10)
    assert not await store.delete(current)
    assert await store.release(current)
    assert await store.load("session") is not None


async def test_reserving_as_same_owner_invalidates_the_previous_token(backend: StorageBackend) -> None:
    store = backend.sessions
    old = await store.reserve("scope", "writer", 10)
    current = await store.reserve("scope", "writer", 10)
    assert old is not None
    assert current is not None
    assert await store.reserve("scope", "other", 10) is None
    assert not await store.abandon(old)
    assert await store.inspect(old) is None
    await backend.advance(10.1)
    assert await store.inspect(current) is None
    assert await store.abandon(current)


async def test_conflicting_retirement_keeps_reservation_and_records(backend: StorageBackend) -> None:
    store = backend.sessions
    await backend.publish("foreign", scope="other")
    original = await backend.publish("original")
    reservation = await store.reserve("scope", "writer", 10)
    assert reservation is not None
    assert (
        await store.commit(
            reservation,
            key="new",
            snapshot_payload="new",
            record_payload="new",
            victims=("original", "foreign"),
            lease_seconds=10,
        )
        is None
    )
    assert await store.inspect(reservation) is not None
    assert await store.save(original, "still", "live")
    assert await store.load("foreign") is not None
    assert (
        await store.commit(
            reservation, key="original", snapshot_payload="new", record_payload="new", victims=(), lease_seconds=10
        )
        is None
    )
    replacement = await store.commit(
        reservation,
        key="original",
        snapshot_payload="new",
        record_payload="new",
        victims=("original", "missing"),
        lease_seconds=10,
    )
    assert replacement is not None
    assert await store.inspect(reservation) is None
    assert not await store.save(original, "stale", "stale")
    assert await store.save(replacement, "current", "current")


async def test_concurrent_admissions_for_one_destination_have_one_winner(backend: StorageBackend) -> None:
    store = backend.sessions
    results: list[ClaimToken | None] = []

    async def admit(scope: str) -> None:
        reservation = await store.reserve(scope, "writer", 30)
        assert reservation is not None
        results.append(
            await store.commit(
                reservation,
                key="shared",
                snapshot_payload="snapshot",
                record_payload="record",
                victims=(),
                lease_seconds=30,
            )
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(admit, "first")
        tasks.start_soon(admit, "second")
    assert sum(token is not None for token in results) == 1
    assert len(await store.list()) == 1


@pytest.mark.parametrize("lease", [0, -1, float("inf"), float("nan")])
async def test_invalid_leases_do_not_consume_reservations(backend: StorageBackend, lease: float) -> None:
    store = backend.sessions
    reservation = await store.reserve("scope", "writer", 10)
    assert reservation is not None
    with pytest.raises(ValueError, match="finite positive"):
        await store.commit(
            reservation,
            key="session",
            snapshot_payload="snapshot",
            record_payload="record",
            victims=(),
            lease_seconds=lease,
        )
    assert await store.inspect(reservation) == ()
    with pytest.raises(ValueError, match="finite positive"):
        await store.claim("missing", "writer", lease)
    with pytest.raises(ValueError, match="finite positive"):
        await store.reserve("other", "writer", lease)


async def test_sqlite_store_serializes_claim_contention(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    writer = SQLiteSessionStore(path)
    reservation = await writer.reserve("scope", "writer", 30)
    assert reservation is not None
    token = await writer.commit(
        reservation, key="session", snapshot_payload="snapshot", record_payload="record", victims=(), lease_seconds=30
    )
    assert token is not None
    assert await writer.release(token)
    contenders = [SQLiteSessionStore(path), SQLiteSessionStore(path)]
    results: list[bool] = []

    async def claim(store: SQLiteSessionStore, owner: str) -> None:
        results.append(await store.claim("session", owner, 30.0) is not None)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claim, contenders[0], "first")
        tasks.start_soon(claim, contenders[1], "second")

    assert sorted(results) == [False, True]


def test_session_store_rejects_unsafe_table_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SQL identifier"):
        SQLiteSessionStore(tmp_path / "sessions.sqlite3", table_name="snapshots; DROP TABLE users")

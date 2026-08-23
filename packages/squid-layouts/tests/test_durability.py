"""Versioned snapshots and fenced durable-session store contracts."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import anyio
import pytest

from squid_layouts import Component, state
from squid_layouts.sources import Position
from squid_layouts.discord import Everyone, Mount
from squid_layouts.discord.durability import (
    ComponentRegistry,
    DurableSessionStore,
    MemorySnapshotStore,
    SnapshotCodec,
    SnapshotError,
    SQLiteSnapshotStore,
)
from squid_layouts.discord.testing import commit_render
from squid_layouts.primitives import Lines, Paginate, Text


class DurableChild(Component):
    entries: tuple[str, ...] = state(factory=lambda: tuple(f"entry {index}" for index in range(6)))

    def render(self):
        return Lines(self.entries, overflow=Paginate(key="items", per=2))


class DurableRoot(Component):
    count: int = state(0)
    transient: object = state(factory=object, persist=False)

    def __init__(self) -> None:
        self.child = DurableChild()

    def render(self):
        return [Text(f"count {self.count}"), self.boundary(self.child, key="child")]


def _registry(*, version: int = 1, migrations=None) -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register("counter", version=version, factory=DurableRoot, migrations=migrations)
    return registry


def _snapshot_store(kind: str, path: Path, clock: Callable[[], float]) -> DurableSessionStore:
    if kind == "memory":
        return MemorySnapshotStore(clock=clock)
    return SQLiteSnapshotStore(path, table_name="durable_sessions", clock=clock)


async def _publish(
    store: DurableSessionStore,
    *,
    key: str,
    scope: str = "scope",
    owner: str = "writer",
    victims: tuple[str, ...] = (),
):
    reservation = await store.reserve(scope, owner, 10.0)
    assert reservation is not None
    token = await store.commit(
        reservation,
        key=key,
        summary_payload=f"summary:{key}",
        snapshot_payload=f"snapshot:{key}",
        victims=victims,
        lease_seconds=10.0,
    )
    assert token is not None
    return token


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_fenced_snapshot_store_contract(kind: str, tmp_path: Path) -> None:
    now = [100.0]
    store = _snapshot_store(kind, tmp_path / "snapshots.sqlite3", lambda: now[0])

    assert await store.load("missing") is None
    first = await _publish(store, key="first", owner="alpha")
    second = await _publish(store, key="second", owner="alpha")
    assert tuple(record.key for record in await store.list_records()) == ("first", "second")
    assert await store.release(first)
    assert await store.release(second)

    alpha = await store.claim("first", "alpha", 10.0)
    assert alpha is not None
    assert await store.claim("first", "beta", 10.0) is None
    now[0] = 110.1
    beta = await store.claim("first", "beta", 10.0)
    assert beta is not None
    assert not await store.renew(alpha, 10.0)
    assert not await store.save(alpha, "stale", "stale")
    assert not await store.delete(alpha)
    assert await store.save(beta, "replacement summary", "replacement snapshot")
    loaded = await store.load("first")
    assert loaded is not None and loaded.snapshot_payload == "replacement snapshot"
    assert await store.delete(beta)
    assert await store.load("first") is None


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_admission_atomically_retires_victims_and_fences_their_writers(kind: str, tmp_path: Path) -> None:
    store = _snapshot_store(kind, tmp_path / "snapshots.sqlite3", lambda: 100.0)
    victim = await _publish(store, key="victim")
    newcomer = await _publish(store, key="newcomer", victims=("victim",))

    assert await store.load("victim") is None
    assert not await store.save(victim, "stale", "stale")
    assert await store.load("newcomer") is not None
    assert await store.renew(newcomer, 10.0)


async def test_lost_admission_token_cannot_publish(tmp_path: Path) -> None:
    now = [100.0]
    store = SQLiteSnapshotStore(tmp_path / "snapshots.sqlite3", clock=lambda: now[0])
    stale = await store.reserve("scope", "first", 10.0)
    assert stale is not None
    now[0] = 110.1
    current = await store.reserve("scope", "second", 10.0)
    assert current is not None

    assert (
        await store.commit(
            stale,
            key="stale",
            summary_payload="summary",
            snapshot_payload="snapshot",
            victims=(),
            lease_seconds=10.0,
        )
        is None
    )
    assert await store.inspect(current) == ()


async def test_sqlite_store_serializes_claim_contention(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.sqlite3"
    writer = SQLiteSnapshotStore(path)
    token = await _publish(writer, key="session")
    assert await writer.release(token)
    contenders = [SQLiteSnapshotStore(path), SQLiteSnapshotStore(path)]
    results: list[bool] = []

    async def claim(store: SQLiteSnapshotStore, owner: str) -> None:
        results.append(await store.claim("session", owner, 30.0) is not None)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claim, contenders[0], "first")
        tasks.start_soon(claim, contenders[1], "second")

    assert sorted(results) == [False, True]


def test_snapshot_store_rejects_unsafe_table_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SQL identifier"):
        SQLiteSnapshotStore(tmp_path / "snapshots.sqlite3", table_name="snapshots; DROP TABLE users")


def test_component_tree_state_and_page_cursors_round_trip_as_canonical_json() -> None:
    root = DurableRoot()
    mount = Mount(root, access=Everyone(), timeout=None)
    commit_render(mount)
    root.count = 7
    root.child.entries = (*root.child.entries, "entry 6")
    commit_render(mount)
    mount.presentation.move_cursor("child.items", Position(offset=2))

    snapshot = _registry().capture(mount, "counter")
    encoded = SnapshotCodec.dumps(snapshot)
    restored = _registry().restore(SnapshotCodec.loads(encoded), access=Everyone(), timeout=None)
    commit_render(restored)
    restored_root = restored.component

    assert isinstance(restored_root, DurableRoot)
    assert restored_root.count == 7
    assert restored_root.child.entries[-1] == "entry 6"
    assert restored.presentation.cursor("child.items").position.offset == 2
    assert "transient" not in next(component.state for component in snapshot.components if component.path == "$")
    assert encoded == SnapshotCodec.dumps(SnapshotCodec.loads(encoded))


def test_non_json_persistent_state_fails_at_capture_boundary() -> None:
    class Invalid(Component):
        value: object = state(factory=object)

        def render(self):
            return Text("invalid")

    registry = ComponentRegistry()
    registry.register("invalid", version=1, factory=Invalid)
    mount = Mount(Invalid(), access=Everyone(), timeout=None)
    commit_render(mount)

    with pytest.raises(SnapshotError, match="not JSON serializable"):
        registry.capture(mount, "invalid")


def test_version_mismatch_requires_a_sequential_migration() -> None:
    mount = Mount(DurableRoot(), access=Everyone(), timeout=None)
    commit_render(mount)
    snapshot = _registry().capture(mount, "counter")

    with pytest.raises(SnapshotError, match="no migration"):
        _registry(version=2).restore(snapshot, access=Everyone())

    migrated = _registry(
        version=2,
        migrations={1: lambda current: replace(current, component_version=2)},
    ).restore(snapshot, access=Everyone())
    assert isinstance(migrated.component, DurableRoot)


def test_migration_must_advance_exactly_one_version() -> None:
    mount = Mount(DurableRoot(), access=Everyone(), timeout=None)
    commit_render(mount)
    snapshot = _registry().capture(mount, "counter")
    registry = _registry(version=2, migrations={1: lambda current: replace(current, component_version=3)})

    with pytest.raises(SnapshotError, match="must produce version 2"):
        registry.restore(snapshot, access=Everyone())

"""Versioned durable state for keyed component trees."""

from dataclasses import replace

import pytest

from squid_layouts import (
    Component,
    ComponentRegistry,
    Lines,
    MemorySnapshotStore,
    Mount,
    MountManager,
    Paginate,
    SnapshotCodec,
    SnapshotError,
    Text,
    state,
)


class DurableChild(Component):
    entries: list[str] = state(factory=lambda: [f"entry {index}" for index in range(6)])

    def render(self):
        return Lines(tuple(self.entries), overflow=Paginate(key="items", per=2))


class DurableRoot(Component):
    count: int = state(0)
    transient: object = state(factory=object, persist=False)

    def __init__(self) -> None:
        self.child = DurableChild()

    def render(self):
        return [Text(f"count {self.count}"), self.embed(self.child, key="child")]


def _registry(*, version: int = 1) -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register("counter", version=version, factory=DurableRoot)
    return registry


def test_component_tree_state_and_page_cursors_round_trip_as_canonical_json() -> None:
    root = DurableRoot()
    mount = Mount(root, timeout=None)
    mount.build_view()
    root.count = 7
    root.child.entries.append("entry 6")
    mount._page["child.items"] = 2

    snapshot = _registry().capture(mount, "counter")
    encoded = SnapshotCodec.dumps(snapshot)
    restored = _registry().restore(SnapshotCodec.loads(encoded), timeout=None)
    restored.build_view()
    restored_root = restored.component

    assert isinstance(restored_root, DurableRoot)
    assert restored_root.count == 7
    assert restored_root.child.entries[-1] == "entry 6"
    assert restored._page["child.items"] == 2
    assert "transient" not in next(component.state for component in snapshot.components if component.path == "$")
    assert encoded == SnapshotCodec.dumps(SnapshotCodec.loads(encoded))


def test_non_json_persistent_state_fails_at_capture_boundary() -> None:
    class Invalid(Component):
        value: object = state(factory=object)

        def render(self):
            return Text("invalid")

    registry = ComponentRegistry()
    registry.register("invalid", version=1, factory=Invalid)
    mount = Mount(Invalid(), timeout=None)
    mount.build_view()

    with pytest.raises(SnapshotError, match="not JSON serializable"):
        registry.capture(mount, "invalid")


def test_version_mismatch_requires_an_explicit_host_migration() -> None:
    mount = Mount(DurableRoot(), timeout=None)
    mount.build_view()
    snapshot = _registry().capture(mount, "counter")

    with pytest.raises(SnapshotError, match="does not match"):
        _registry(version=2).restore(replace(snapshot, component_version=1))


async def test_mount_manager_checkpoints_and_restores_through_a_host_store() -> None:
    registry = _registry()
    store = MemorySnapshotStore()
    root = DurableRoot()
    mount = Mount(root, timeout=None)
    mount.build_view()
    root.count = 11

    writer = MountManager(registry, store)
    writer.attach("session", "counter", mount)
    await writer.checkpoint("session")

    reader = MountManager(registry, store)
    restored = await reader.restore("session", timeout=None)

    assert restored is not None
    assert isinstance(restored.component, DurableRoot)
    assert restored.component.count == 11
    assert reader.get("session") is restored

    await reader.finish("session")
    assert await store.load("session") is None


async def test_restoring_an_absent_session_is_a_clean_miss() -> None:
    manager = MountManager(_registry(), MemorySnapshotStore())
    assert await manager.restore("missing") is None

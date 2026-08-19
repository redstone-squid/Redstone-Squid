"""Opt-in durable component snapshots and host-owned mount management."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from squid_layouts.component import Component, render_component_tree
from squid_layouts.mount import Mount
from squid_layouts.reactivity import export_state, restore_state


class SnapshotError(ValueError):
    """A snapshot is malformed, incompatible, or unsafe to restore."""


@dataclass(frozen=True, slots=True)
class ComponentSnapshot:
    path: str
    type_id: str
    state: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MountSnapshot:
    protocol: int
    component_key: str
    component_version: int
    components: tuple[ComponentSnapshot, ...]
    pages: Mapping[str, int]


class SnapshotCodec:
    """Canonical JSON codec for durable mount state protocol 0."""

    protocol = 0

    @classmethod
    def dumps(cls, snapshot: MountSnapshot) -> str:
        if snapshot.protocol != cls.protocol:
            message = f"unsupported mount snapshot protocol {snapshot.protocol}"
            raise SnapshotError(message)
        raw = {
            "protocol": snapshot.protocol,
            "component_key": snapshot.component_key,
            "component_version": snapshot.component_version,
            "components": [
                {"path": component.path, "type_id": component.type_id, "state": dict(component.state)}
                for component in snapshot.components
            ],
            "pages": dict(snapshot.pages),
        }
        try:
            return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            message = f"component state is not JSON serializable: {error}"
            raise SnapshotError(message) from error

    @classmethod
    def loads(cls, payload: str) -> MountSnapshot:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SnapshotError(str(error)) from error
        if not isinstance(raw, dict):
            message = "mount snapshot must be an object"
            raise SnapshotError(message)
        protocol = _integer(raw, "protocol")
        if protocol != cls.protocol:
            message = f"unsupported mount snapshot protocol {protocol}"
            raise SnapshotError(message)
        components = raw.get("components")
        pages = raw.get("pages")
        if not isinstance(components, list) or not isinstance(pages, dict):
            message = "mount snapshot components and pages are malformed"
            raise SnapshotError(message)
        decoded_components: list[ComponentSnapshot] = []
        for value in components:
            item = _object(value)
            state = item.get("state")
            if not isinstance(state, dict) or not all(isinstance(key, str) for key in state):
                message = "component snapshot state must be an object with string keys"
                raise SnapshotError(message)
            decoded_components.append(
                ComponentSnapshot(
                    path=_string(item, "path"),
                    type_id=_string(item, "type_id"),
                    state=state,
                )
            )
        if not all(
            isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
            for key, value in pages.items()
        ):
            message = "mount snapshot pages must map strings to integers"
            raise SnapshotError(message)
        return MountSnapshot(
            protocol,
            _string(raw, "component_key"),
            _integer(raw, "component_version"),
            tuple(decoded_components),
            pages,
        )


@dataclass(frozen=True, slots=True)
class _Registration:
    version: int
    factory: Callable[[], Component]


class ComponentRegistry:
    """Host registry that restores known root components without dynamic imports."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(self, key: str, *, version: int, factory: Callable[[], Component]) -> None:
        if not key or version < 1:
            message = "durable component keys must be non-empty and versions positive"
            raise ValueError(message)
        if key in self._registrations:
            message = f"durable component {key!r} is already registered"
            raise ValueError(message)
        self._registrations[key] = _Registration(version, factory)

    def capture(self, mount: Mount, component_key: str) -> MountSnapshot:
        registration = self._registrations.get(component_key)
        if registration is None:
            message = f"durable component {component_key!r} is not registered"
            raise SnapshotError(message)
        if not mount._components:
            message = "a mount must be built before it can be snapshotted"
            raise SnapshotError(message)
        components = tuple(
            ComponentSnapshot(path, _type_id(component), export_state(component))
            for path, component in sorted(mount._components.items())
        )
        snapshot = MountSnapshot(
            SnapshotCodec.protocol,
            component_key,
            registration.version,
            components,
            dict(mount._page),
        )
        SnapshotCodec.dumps(snapshot)
        return snapshot

    def restore(self, snapshot: MountSnapshot, **mount_options: Any) -> Mount:
        registration = self._registrations.get(snapshot.component_key)
        if registration is None:
            message = f"durable component {snapshot.component_key!r} is not registered"
            raise SnapshotError(message)
        if registration.version != snapshot.component_version:
            message = (
                f"durable component {snapshot.component_key!r} snapshot version "
                f"{snapshot.component_version} does not match {registration.version}"
            )
            raise SnapshotError(message)
        by_path = {component.path: component for component in snapshot.components}
        root = registration.factory()
        root_snapshot = by_path.get("$")
        if root_snapshot is None:
            message = "mount snapshot has no root component"
            raise SnapshotError(message)
        _restore_component(root, root_snapshot)
        mount = Mount(root, **mount_options)
        tree = render_component_tree(root)
        if set(by_path) != set(tree.components):
            message = "restored component tree does not match the snapshot paths"
            raise SnapshotError(message)
        for path, component in tree.components.items():
            if path != "$":
                _restore_component(component, by_path[path])
        mount._page = dict(snapshot.pages)
        return mount


class SnapshotStore(Protocol):
    """Host persistence boundary for encoded mount snapshots."""

    async def load(self, key: str) -> str | None: ...

    async def save(self, key: str, payload: str) -> None: ...

    async def delete(self, key: str) -> None: ...


class MemorySnapshotStore:
    """Small store for tests and single-process development."""

    def __init__(self) -> None:
        self._payloads: dict[str, str] = {}

    async def load(self, key: str) -> str | None:
        return self._payloads.get(key)

    async def save(self, key: str, payload: str) -> None:
        self._payloads[key] = payload

    async def delete(self, key: str) -> None:
        self._payloads.pop(key, None)


class MountManager:
    """Own active mounts and explicit checkpoints; it never starts background tasks."""

    def __init__(self, registry: ComponentRegistry, store: SnapshotStore) -> None:
        self.registry = registry
        self.store = store
        self._active: dict[str, tuple[str, Mount]] = {}

    def attach(self, key: str, component_key: str, mount: Mount) -> None:
        if key in self._active:
            message = f"mount {key!r} is already active"
            raise ValueError(message)
        self._active[key] = (component_key, mount)

    def get(self, key: str) -> Mount | None:
        active = self._active.get(key)
        return active[1] if active is not None else None

    async def checkpoint(self, key: str) -> MountSnapshot:
        active = self._active.get(key)
        if active is None:
            message = f"mount {key!r} is not active"
            raise KeyError(message)
        component_key, mount = active
        snapshot = self.registry.capture(mount, component_key)
        await self.store.save(key, SnapshotCodec.dumps(snapshot))
        return snapshot

    async def restore(self, key: str, **mount_options: Any) -> Mount | None:
        payload = await self.store.load(key)
        if payload is None:
            return None
        snapshot = SnapshotCodec.loads(payload)
        mount = self.registry.restore(snapshot, **mount_options)
        self._active[key] = (snapshot.component_key, mount)
        return mount

    async def finish(self, key: str, *, delete: bool = True) -> None:
        active = self._active.pop(key, None)
        if active is not None:
            await active[1].finish()
        if delete:
            await self.store.delete(key)


def _restore_component(component: Component, snapshot: ComponentSnapshot) -> None:
    expected = _type_id(component)
    if snapshot.type_id != expected:
        message = f"component at {snapshot.path!r} changed type from {snapshot.type_id!r} to {expected!r}"
        raise SnapshotError(message)
    try:
        restore_state(component, snapshot.state)
    except ValueError as error:
        raise SnapshotError(str(error)) from error


def _type_id(component: Component) -> str:
    cls = type(component)
    return f"{cls.__module__}:{cls.__qualname__}"


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = "snapshot entry must be an object"
        raise SnapshotError(message)
    return value


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"snapshot field {key!r} must be a string"
        raise SnapshotError(message)
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"snapshot field {key!r} must be an integer"
        raise SnapshotError(message)
    return value

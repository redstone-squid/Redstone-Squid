"""Opt-in durable component snapshots and host-owned mount management."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from squid_layouts.component import Component, render_component_tree
from squid_layouts.mount import Mount
from squid_layouts.presentation import (
    CursorState,
    DisclosureState,
    PresentationSession,
    SelectionState,
    StrategyState,
)
from squid_layouts.reactivity import export_state, restore_state


class SnapshotError(ValueError):
    """A snapshot is malformed, incompatible, or unsafe to restore."""


@dataclass(frozen=True, slots=True)
class ComponentSnapshot:
    path: str
    type_id: str
    state: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PresentationSnapshot:
    cursors: Mapping[str, CursorState]
    selections: Mapping[str, SelectionState]
    disclosures: Mapping[str, DisclosureState]
    strategies: Mapping[str, StrategyState]


@dataclass(frozen=True, slots=True)
class MountSnapshot:
    protocol: int
    component_key: str
    component_version: int
    components: tuple[ComponentSnapshot, ...]
    presentation: PresentationSnapshot


class SnapshotCodec:
    """Canonical JSON codec for durable mount state protocol 1."""

    protocol = 1

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
            "presentation": _presentation_to_dict(snapshot.presentation),
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
        presentation = raw.get("presentation")
        if not isinstance(components, list) or not isinstance(presentation, dict):
            message = "mount snapshot components or presentation are malformed"
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
        return MountSnapshot(
            protocol,
            _string(raw, "component_key"),
            _integer(raw, "component_version"),
            tuple(decoded_components),
            _presentation_from_dict(presentation),
        )


def _presentation_to_dict(snapshot: PresentationSnapshot) -> dict[str, object]:
    return {
        "cursors": {
            key: {
                "index": cursor.index,
                "anchor": cursor.anchor,
                "extent": cursor.extent,
                "content_fingerprint": cursor.content_fingerprint,
            }
            for key, cursor in snapshot.cursors.items()
        },
        "selections": {key: {"selected": list(selection.selected)} for key, selection in snapshot.selections.items()},
        "disclosures": {key: {"open": disclosure.open} for key, disclosure in snapshot.disclosures.items()},
        "strategies": {
            key: {
                "node_key": strategy.node_key,
                "adapter_id": strategy.adapter_id,
                "adapter_version": strategy.adapter_version,
                "strategy_id": strategy.strategy_id,
            }
            for key, strategy in snapshot.strategies.items()
        },
    }


def _presentation_from_dict(raw: Mapping[str, object]) -> PresentationSnapshot:
    cursors = _object(raw.get("cursors"))
    selections = _object(raw.get("selections"))
    disclosures = _object(raw.get("disclosures"))
    strategies = _object(raw.get("strategies"))
    return PresentationSnapshot(
        cursors={
            key: CursorState(
                _integer(item := _object(value), "index"),
                _optional_string(item, "anchor"),
                _integer(item, "extent"),
                _string(item, "content_fingerprint"),
            )
            for key, value in cursors.items()
        },
        selections={key: SelectionState(_strings(_object(value), "selected")) for key, value in selections.items()},
        disclosures={key: DisclosureState(_boolean(_object(value), "open")) for key, value in disclosures.items()},
        strategies={
            key: StrategyState(
                _string(item := _object(value), "node_key"),
                _string(item, "adapter_id"),
                _integer(item, "adapter_version"),
                _string(item, "strategy_id"),
            )
            for key, value in strategies.items()
        },
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
        if not mount.runtime.components:
            message = "a mount must be built before it can be snapshotted"
            raise SnapshotError(message)
        components = tuple(
            ComponentSnapshot(path, _type_id(component), export_state(component))
            for path, component in sorted(mount.runtime.components.items())
        )
        snapshot = MountSnapshot(
            SnapshotCodec.protocol,
            component_key,
            registration.version,
            components,
            PresentationSnapshot(
                dict(mount.presentation.cursors),
                dict(mount.presentation.selections),
                dict(mount.presentation.disclosures),
                dict(mount.presentation.strategies),
            ),
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
        mount.presentation = PresentationSession(
            cursors=dict(snapshot.presentation.cursors),
            selections=dict(snapshot.presentation.selections),
            disclosures=dict(snapshot.presentation.disclosures),
            strategies=dict(snapshot.presentation.strategies),
        )
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


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        message = f"snapshot field {key!r} must be a string or null"
        raise SnapshotError(message)
    return value


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        message = f"snapshot field {key!r} must be a boolean"
        raise SnapshotError(message)
    return value


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"snapshot field {key!r} must be an array of strings"
        raise SnapshotError(message)
    return tuple(value)

"""Opt-in durable component snapshots and host-owned mount management."""

import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from squid_layouts.discord.mount import Mount
from squid_layouts.discord.sessions import SessionKey
from squid_layouts.runtime.component import Component, render_component_tree
from squid_layouts.runtime.presentation import (
    CursorState,
    DisclosureState,
    PresentationSession,
    SelectionState,
    StrategyState,
)
from squid_layouts.runtime.reactivity import export_state, restore_state
from squid_layouts.sources import Direction, Position

from .postgres import PostgresSnapshotStore
from .stores import SQLiteSnapshotStore

__all__ = [
    "ComponentRegistry",
    "ComponentSnapshot",
    "DurableMountCodec",
    "DurableMountRecord",
    "LeaseSnapshotStore",
    "MemorySnapshotStore",
    "MountLocator",
    "MountLocatorResolver",
    "MountManager",
    "MountReachability",
    "MountSnapshot",
    "PostgresSnapshotStore",
    "PresentationSnapshot",
    "RecoveredMount",
    "SQLiteSnapshotStore",
    "SnapshotCodec",
    "SnapshotError",
    "SnapshotStore",
]


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


@dataclass(frozen=True, slots=True)
class MountLocator:
    """Frontend-neutral coordinates needed to reconnect a rendered message."""

    frontend: str
    values: Mapping[str, str | int]


class MountReachability(StrEnum):
    """Result of resolving a persisted frontend locator during recovery."""

    REACHABLE = "reachable"
    MISSING = "missing"
    UNREACHABLE = "unreachable"


class MountLocatorResolver(Protocol):
    """Host boundary for checking whether a persisted frontend still exists."""

    async def resolve(self, locator: MountLocator) -> MountReachability: ...


@dataclass(frozen=True, slots=True)
class DurableMountRecord:
    """A component snapshot plus operational recovery metadata."""

    protocol: int
    snapshot: MountSnapshot
    locator: MountLocator
    expires_at: float | None = None


class DurableMountCodec:
    """Canonical JSON codec for operational mount record protocol 1."""

    protocol = 1

    @classmethod
    def dumps(cls, record: DurableMountRecord) -> str:
        if record.protocol != cls.protocol:
            message = f"unsupported durable mount record protocol {record.protocol}"
            raise SnapshotError(message)
        raw = {
            "protocol": record.protocol,
            "snapshot": json.loads(SnapshotCodec.dumps(record.snapshot)),
            "locator": {
                "frontend": record.locator.frontend,
                "values": dict(record.locator.values),
            },
            "expires_at": record.expires_at,
        }
        try:
            return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            message = f"durable mount metadata is not JSON serializable: {error}"
            raise SnapshotError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableMountRecord:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SnapshotError(str(error)) from error
        item = _object(raw)
        protocol = _integer(item, "protocol")
        if protocol != cls.protocol or "snapshot" not in item:
            message = f"unsupported durable mount record protocol {protocol}"
            raise SnapshotError(message)
        locator = _object(item.get("locator"))
        values = _object(locator.get("values"))
        if not all(isinstance(key, str) and isinstance(value, str | int) for key, value in values.items()):
            message = "mount locator values must contain string keys and string or integer values"
            raise SnapshotError(message)
        expires_at = item.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, int | float):
            message = "mount record expires_at must be a number or null"
            raise SnapshotError(message)
        snapshot_payload = json.dumps(item.get("snapshot"), ensure_ascii=False, separators=(",", ":"))
        return DurableMountRecord(
            protocol,
            SnapshotCodec.loads(snapshot_payload),
            MountLocator(_string(locator, "frontend"), values),
            float(expires_at) if expires_at is not None else None,
        )


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
                "position": {
                    "anchor": cursor.position.anchor,
                    "offset": cursor.position.offset,
                    "direction": cursor.position.direction.value,
                },
                "extent": cursor.extent,
                "fingerprint": cursor.fingerprint,
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
                Position(
                    _optional_string(position := _object((item := _object(value)).get("position")), "anchor"),
                    _integer(position, "offset"),
                    Direction(_string(position, "direction")),
                ),
                _integer(item, "extent"),
                _string(item, "fingerprint"),
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
class RestoreContext:
    """Stable operational facts available while a registered mount is reconstructed."""

    record_id: str
    session_key: SessionKey
    actor_id: int | None
    mount_actor_id: int | None
    locator: MountLocator
    expires_at: float | None
    parent_id: str | None


type SnapshotMigration = Callable[[MountSnapshot], MountSnapshot]


@dataclass(frozen=True, slots=True)
class _Registration:
    version: int
    factory: Callable[[], Component] | None
    restore: Callable[[RestoreContext], Mount] | None
    migrations: Mapping[int, SnapshotMigration]


class ComponentRegistry:
    """Host registry that restores known root components without dynamic imports."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        key: str,
        *,
        version: int,
        restore: Callable[[RestoreContext], Mount] | None = None,
        migrations: Mapping[int, SnapshotMigration] | None = None,
        factory: Callable[[], Component] | None = None,
    ) -> None:
        """Register one known root and every sequential migration to its current version.

        `restore` is the durable-session path: it reconstructs the complete mount, including
        its explicit access policy and host dependencies. `factory` remains the low-level
        component-only path for direct snapshot codec use.
        """
        if not key or version < 1:
            message = "durable component keys must be non-empty and versions positive"
            raise ValueError(message)
        if (restore is None) == (factory is None):
            message = "register exactly one of restore or factory"
            raise ValueError(message)
        if key in self._registrations:
            message = f"durable component {key!r} is already registered"
            raise ValueError(message)
        migration_map = {} if migrations is None else dict(migrations)
        if any(source < 1 or source >= version for source in migration_map):
            message = "snapshot migration sources must be positive versions below the registered version"
            raise ValueError(message)
        self._registrations[key] = _Registration(version, factory, restore, migration_map)

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

    def restore(
        self,
        snapshot: MountSnapshot,
        context: RestoreContext | None = None,
        **mount_options: Any,
    ) -> Mount:
        registration = self._registrations.get(snapshot.component_key)
        if registration is None:
            message = f"durable component {snapshot.component_key!r} is not registered"
            raise SnapshotError(message)
        snapshot = self._migrate(snapshot, registration)
        by_path = {component.path: component for component in snapshot.components}
        if registration.restore is not None:
            if context is None:
                message = f"durable component {snapshot.component_key!r} requires a restore context"
                raise SnapshotError(message)
            mount = registration.restore(context)
            root = mount.component
        else:
            factory = registration.factory
            if factory is None:
                message = f"durable component {snapshot.component_key!r} has no restore recipe"
                raise SnapshotError(message)
            root = factory()
            mount = Mount(root, **mount_options)
        root_snapshot = by_path.get("$")
        if root_snapshot is None:
            message = "mount snapshot has no root component"
            raise SnapshotError(message)
        _restore_component(root, root_snapshot)
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

    @staticmethod
    def _migrate(snapshot: MountSnapshot, registration: _Registration) -> MountSnapshot:
        current = snapshot
        while current.component_version < registration.version:
            migration = registration.migrations.get(current.component_version)
            if migration is None:
                message = (
                    f"durable component {current.component_key!r} has no migration from "
                    f"version {current.component_version}"
                )
                raise SnapshotError(message)
            source_version = current.component_version
            migrated = migration(current)
            if (
                migrated.protocol != SnapshotCodec.protocol
                or migrated.component_key != current.component_key
                or migrated.component_version != source_version + 1
            ):
                message = f"snapshot migration from version {source_version} must produce version {source_version + 1}"
                raise SnapshotError(message)
            SnapshotCodec.dumps(migrated)
            current = SnapshotCodec.loads(SnapshotCodec.dumps(migrated))
        if current.component_version != registration.version:
            message = (
                f"durable component {current.component_key!r} snapshot version "
                f"{current.component_version} is newer than registered version {registration.version}"
            )
            raise SnapshotError(message)
        return current


class SnapshotStore(Protocol):
    """Host persistence boundary for encoded mount snapshots."""

    async def load(self, key: str) -> str | None: ...

    async def save(self, key: str, payload: str) -> None: ...

    async def delete(self, key: str) -> None: ...


@runtime_checkable
class LeaseSnapshotStore(SnapshotStore, Protocol):
    """Optional store contract for distributed startup recovery ownership."""

    async def list_keys(self) -> tuple[str, ...]: ...

    async def claim(self, key: str, owner: str, lease_until: float) -> bool: ...

    async def renew(self, key: str, owner: str, lease_until: float) -> bool: ...

    async def release(self, key: str, owner: str) -> None: ...


class MemorySnapshotStore:
    """Small store for tests and single-process development."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._payloads: dict[str, str] = {}
        self._leases: dict[str, tuple[str, float]] = {}
        self._clock = clock

    async def load(self, key: str) -> str | None:
        return self._payloads.get(key)

    async def save(self, key: str, payload: str) -> None:
        self._payloads[key] = payload

    async def delete(self, key: str) -> None:
        self._payloads.pop(key, None)
        self._leases.pop(key, None)

    async def list_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._payloads))

    async def claim(self, key: str, owner: str, lease_until: float) -> bool:
        if key not in self._payloads:
            return False
        current = self._leases.get(key)
        if current is not None and current[0] != owner and current[1] >= self._clock():
            return False
        self._leases[key] = (owner, lease_until)
        return True

    async def renew(self, key: str, owner: str, lease_until: float) -> bool:
        current = self._leases.get(key)
        if current is None or current[0] != owner or key not in self._payloads:
            return False
        self._leases[key] = (owner, lease_until)
        return True

    async def release(self, key: str, owner: str) -> None:
        if (current := self._leases.get(key)) is not None and current[0] == owner:
            self._leases.pop(key, None)


@dataclass(frozen=True, slots=True)
class RecoveredMount:
    """A restored mount beside the frontend coordinates the host must reconnect."""

    key: str
    mount: Mount
    locator: MountLocator | None


@dataclass(slots=True)
class _ActiveMount:
    component_key: str
    mount: Mount
    locator: MountLocator | None = None
    expires_at: float | None = None


class MountManager:
    """Own checkpoints and optional leased recovery; it never starts background tasks."""

    def __init__(
        self,
        registry: ComponentRegistry,
        store: SnapshotStore,
        *,
        owner: str | None = None,
        lease_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
        locator_resolver: MountLocatorResolver | None = None,
    ) -> None:
        if lease_seconds <= 0:
            message = "mount recovery lease must be positive"
            raise ValueError(message)
        self.registry = registry
        self.store = store
        self.owner = owner if owner is not None else secrets.token_urlsafe(12)
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.locator_resolver = locator_resolver
        self._active: dict[str, _ActiveMount] = {}
        self._claimed: set[str] = set()

    def attach(
        self,
        key: str,
        component_key: str,
        mount: Mount,
        *,
        locator: MountLocator | None = None,
        expires_at: float | None = None,
    ) -> None:
        if key in self._active:
            message = f"mount {key!r} is already active"
            raise ValueError(message)
        self._active[key] = _ActiveMount(component_key, mount, locator, expires_at)

    def get(self, key: str) -> Mount | None:
        active = self._active.get(key)
        return active.mount if active is not None else None

    def get_locator(self, key: str) -> MountLocator | None:
        active = self._active.get(key)
        return active.locator if active is not None else None

    async def checkpoint(self, key: str) -> MountSnapshot:
        active = self._active.get(key)
        if active is None:
            message = f"mount {key!r} is not active"
            raise KeyError(message)
        if key in self._claimed and not await self._renew(key):
            message = f"mount {key!r} lost its recovery lease before checkpoint"
            raise SnapshotError(message)
        snapshot = self.registry.capture(active.mount, active.component_key)
        if active.locator is None:
            payload = SnapshotCodec.dumps(snapshot)
        else:
            payload = DurableMountCodec.dumps(
                DurableMountRecord(DurableMountCodec.protocol, snapshot, active.locator, active.expires_at)
            )
        await self.store.save(key, payload)
        return snapshot

    async def restore(self, key: str, **mount_options: Any) -> Mount | None:
        payload = await self.store.load(key)
        if payload is None:
            return None
        snapshot, locator, expires_at = _decode_stored_mount(payload)
        if expires_at is not None and expires_at <= self.clock():
            await self.store.delete(key)
            self._claimed.discard(key)
            return None
        mount = self.registry.restore(snapshot, **mount_options)
        self._active[key] = _ActiveMount(snapshot.component_key, mount, locator, expires_at)
        return mount

    async def recover(self, **mount_options: Any) -> tuple[RecoveredMount, ...]:
        """Claim and restore every available record for host-driven frontend reconnection."""
        if not isinstance(self.store, LeaseSnapshotStore):
            message = "startup recovery requires a LeaseSnapshotStore"
            raise TypeError(message)
        recovered: list[RecoveredMount] = []
        for key in await self.store.list_keys():
            claimed = await self.store.claim(key, self.owner, self.clock() + self.lease_seconds)
            if not claimed:
                continue
            self._claimed.add(key)
            try:
                payload = await self.store.load(key)
                if payload is None:
                    self._claimed.discard(key)
                    await self.store.release(key, self.owner)
                    continue
                _, locator, expires_at = _decode_stored_mount(payload)
                if expires_at is not None and expires_at <= self.clock():
                    await self.store.delete(key)
                    self._claimed.discard(key)
                    continue
                if locator is not None:
                    reachability = await self._resolve(locator)
                    if reachability is MountReachability.MISSING:
                        await self.store.delete(key)
                        self._claimed.discard(key)
                        continue
                    if reachability is MountReachability.UNREACHABLE:
                        self._claimed.discard(key)
                        await self.store.release(key, self.owner)
                        continue
                mount = await self.restore(key, **mount_options)
            except Exception:
                self._claimed.discard(key)
                await self.store.release(key, self.owner)
                raise
            if mount is None:
                self._claimed.discard(key)
                await self.store.release(key, self.owner)
                continue
            recovered.append(RecoveredMount(key, mount, self.get_locator(key)))
        return tuple(recovered)

    async def _resolve(self, locator: MountLocator) -> MountReachability:
        if self.locator_resolver is None:
            return MountReachability.UNREACHABLE
        try:
            return await self.locator_resolver.resolve(locator)
        except Exception:
            return MountReachability.UNREACHABLE

    async def renew_claims(self) -> tuple[str, ...]:
        """Renew owned leases and return keys whose ownership was lost."""
        if not isinstance(self.store, LeaseSnapshotStore):
            return ()
        lost: list[str] = []
        for key in tuple(sorted(self._claimed)):
            if not await self._renew(key):
                self._claimed.discard(key)
                if (active := self._active.pop(key, None)) is not None:
                    await active.mount.finish()
                lost.append(key)
        return tuple(lost)

    async def _renew(self, key: str) -> bool:
        if not isinstance(self.store, LeaseSnapshotStore):
            return False
        return await self.store.renew(key, self.owner, self.clock() + self.lease_seconds)

    async def finish(self, key: str, *, delete: bool = True) -> None:
        active = self._active.pop(key, None)
        if active is not None:
            await active.mount.finish()
        if delete:
            await self.store.delete(key)
        if key in self._claimed and isinstance(self.store, LeaseSnapshotStore):
            await self.store.release(key, self.owner)
        self._claimed.discard(key)


def _decode_stored_mount(payload: str) -> tuple[MountSnapshot, MountLocator | None, float | None]:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SnapshotError(str(error)) from error
    if isinstance(raw, dict) and "snapshot" in raw:
        record = DurableMountCodec.loads(payload)
        return record.snapshot, record.locator, record.expires_at
    return SnapshotCodec.loads(payload), None, None


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

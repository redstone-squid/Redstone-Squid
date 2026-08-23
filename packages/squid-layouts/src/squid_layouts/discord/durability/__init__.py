"""Opt-in durable component snapshots and host-owned mount management."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from squid_layouts.discord.mount import Mount
from squid_layouts.discord.sessions import SessionKey
from squid_layouts.discord.target import V2_TARGET
from squid_layouts.discord.targets import DEFAULT_TARGETS, TargetRegistry
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

from .postgres import PostgresSnapshotStore, PostgresTopicBridge, TopicBridgeSnapshot
from .stores import (
    AdmissionToken,
    ClaimToken,
    DurableSessionStore,
    MemorySnapshotStore,
    SQLiteSnapshotStore,
    StoredSessionRecord,
)

__all__ = [
    "DEFAULT_TARGETS",
    "AdmissionToken",
    "ClaimToken",
    "ComponentRegistry",
    "ComponentSnapshot",
    "DiscordFrontend",
    "DurabilityHealth",
    "DurableBot",
    "DurableFrontend",
    "DurableMountCodec",
    "DurableMountRecord",
    "DurableMountState",
    "DurableOpenResult",
    "DurableRuntimeSnapshot",
    "DurableSession",
    "DurableSessionCodec",
    "DurableSessionRecord",
    "DurableSessionRuntime",
    "DurableSessionStore",
    "MemorySnapshotStore",
    "Missing",
    "MountLocator",
    "MountSnapshot",
    "NotDurable",
    "PostgresSnapshotStore",
    "PostgresTopicBridge",
    "PresentationSnapshot",
    "Promoted",
    "PurgeResult",
    "Reconnected",
    "RecoveredBinding",
    "RecoveryItem",
    "RecoveryReport",
    "RestoreContext",
    "SQLiteSnapshotStore",
    "SnapshotCodec",
    "SnapshotError",
    "StoredSessionRecord",
    "TargetRegistry",
    "TopicBridgeSnapshot",
    "Unreachable",
    "encode_session_scope",
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
    target_id: str = V2_TARGET.id
    target_version: int = V2_TARGET.version
    target_fingerprint: str = ""
    """A digest of the profile this mount was planned against.

    Recorded beside the id because an id alone does not pin the budgets. Recovery refuses a
    profile that has since changed rather than rebuilding a stored render against limits it
    was never fitted to.
    """


@dataclass(frozen=True, slots=True)
class MountLocator:
    """Frontend-neutral coordinates needed to reconnect a rendered message."""

    frontend: str
    values: Mapping[str, str | int]


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
    """Canonical JSON codec for durable mount state protocol 2.

    Protocol 2 adds the target identity. A protocol 1 record has no way to say which message
    mode it was planned for, so it is refused rather than assumed to be Components V2.
    """

    protocol = 2

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
            "target": {
                "id": snapshot.target_id,
                "version": snapshot.target_version,
                "fingerprint": snapshot.target_fingerprint,
            },
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
        target = _object(raw.get("target"))
        return MountSnapshot(
            protocol,
            _string(raw, "component_key"),
            _integer(raw, "component_version"),
            tuple(decoded_components),
            _presentation_from_dict(presentation),
            _string(target, "id"),
            _integer(target, "version"),
            _string(target, "fingerprint"),
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

    def has(self, key: str) -> bool:
        """Whether a restore recipe is registered under `key`."""
        return key in self._registrations

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
            mount.target.id,
            mount.target.version,
            mount.target.fingerprint,
        )
        SnapshotCodec.dumps(snapshot)
        return snapshot

    def restore(
        self,
        snapshot: MountSnapshot,
        context: RestoreContext | None = None,
        *,
        targets: TargetRegistry = DEFAULT_TARGETS,
        **mount_options: Any,
    ) -> Mount:
        """Rebuild a mount from a snapshot, against the exact target it was planned for.

        The target is resolved *before* anything is built, so an unavailable or changed
        profile fails while the record is still just data — not after a mount exists and a
        reader could already be clicking it.
        """
        target = targets.resolve(snapshot.target_id, snapshot.target_version, snapshot.target_fingerprint)
        mount_options.setdefault("target", target)
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


from .bot import DurableBot
from .frontend import (
    DiscordFrontend,
    DurableFrontend,
    Missing,
    NotDurable,
    Promoted,
    Reconnected,
    RecoveredBinding,
    Unreachable,
)
from .runtime import (
    DurabilityHealth,
    DurableOpenResult,
    DurableRuntimeSnapshot,
    DurableSession,
    DurableSessionRuntime,
    PurgeResult,
    RecoveryItem,
    RecoveryReport,
)
from .session_records import (
    DurableMountState,
    DurableSessionCodec,
    DurableSessionRecord,
    encode_session_scope,
)

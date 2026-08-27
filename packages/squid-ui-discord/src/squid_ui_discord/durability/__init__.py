"""Opt-in durable component snapshots and host-owned mount management."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from squid_storage import (
    AdmissionToken,
    ClaimToken,
    DurableSessionStore,
    MemorySessionStore,
    PostgresSessionStore,
    PostgresTopicBridge,
    SessionRecord,
    SQLiteSessionStore,
    TopicBridgeSnapshot,
)
from squid_ui.runtime.component import AnyComponent, Component, render_component_tree
from squid_ui.runtime.presentation_state import (
    CursorState,
    DisclosureState,
    PresentationState,
    SelectionState,
    StrategyState,
)
from squid_ui.runtime.reactivity import export_state, restore_state
from squid_ui.sources import Direction, Position
from squid_ui_discord.message_root import AnyMessageRoot, MessageRoot
from squid_ui_discord.sessions import SessionKey
from squid_ui_discord.target import DISCORD_V2_DPY27
from squid_ui_discord.targets import DEFAULT_TARGETS, TargetRegistry

__all__ = [
    "DEFAULT_TARGETS",
    "AdmissionToken",
    "ClaimToken",
    "ComponentRegistry",
    "ComponentState",
    "DiscordFrontend",
    "DurabilityHealth",
    "DurableBot",
    "DurableFrontend",
    "DurableMessageRootCodec",
    "DurableMessageRootRecord",
    "DurableOpenResult",
    "DurableRuntimeSnapshot",
    "DurableSession",
    "DurableSessionCodec",
    "DurableSessionRecord",
    "DurableSessionRuntime",
    "DurableSessionStore",
    "FrontendAddress",
    "MemorySessionStore",
    "MessageRootState",
    "MessageRootStateCodec",
    "MessageRootStateError",
    "Missing",
    "NotDurable",
    "PostgresSessionStore",
    "PostgresTopicBridge",
    "PresentationSnapshot",
    "Promoted",
    "PurgeResult",
    "Reconnected",
    "RecoveredBinding",
    "RecoveryItem",
    "RecoveryReport",
    "RestoreContext",
    "SQLiteSessionStore",
    "SessionRecord",
    "SessionRootRecord",
    "TargetRegistry",
    "TopicBridgeSnapshot",
    "Unreachable",
    "encode_session_scope",
    "migrate_component_state",
]


class MessageRootStateError(ValueError):
    """A snapshot is malformed, incompatible, or unsafe to restore."""


@dataclass(frozen=True, slots=True)
class ComponentState:
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
class MessageRootState:
    protocol: int
    component_key: str
    component_version: int
    components: tuple[ComponentState, ...]
    presentation: PresentationSnapshot
    target_triple: str = DISCORD_V2_DPY27.triple
    target_version: int = DISCORD_V2_DPY27.version
    target_fingerprint: str = ""
    target_adapter_capabilities: tuple[str, ...] = ()
    """A digest of the target this mount was planned against.

    Recorded beside the triple because neither axis alone pins the budgets. Recovery refuses
    a target that has since changed rather than rebuilding a stored render against limits it
    was never fitted to.
    """


@dataclass(frozen=True, slots=True)
class FrontendAddress:
    """Frontend-neutral coordinates needed to reconnect a rendered message."""

    frontend: str
    values: Mapping[str, str | int]


@dataclass(frozen=True, slots=True)
class DurableMessageRootRecord:
    """One mount's stored state plus the operational metadata recovery needs."""

    protocol: int
    state: MessageRootState
    address: FrontendAddress
    expires_at: float | None = None


class DurableMessageRootCodec:
    """Canonical JSON codec for operational mount record protocol 1."""

    protocol = 1

    @classmethod
    def dumps(cls, record: DurableMessageRootRecord) -> str:
        if record.protocol != cls.protocol:
            message = f"unsupported durable mount record protocol {record.protocol}"
            raise MessageRootStateError(message)
        raw = {
            "protocol": record.protocol,
            "state": json.loads(MessageRootStateCodec.dumps(record.state)),
            "address": {
                "frontend": record.address.frontend,
                "values": dict(record.address.values),
            },
            "expires_at": record.expires_at,
        }
        try:
            return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            message = f"durable mount metadata is not JSON serializable: {error}"
            raise MessageRootStateError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableMessageRootRecord:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise MessageRootStateError(str(error)) from error
        item = _object(raw)
        protocol = _integer(item, "protocol")
        if protocol != cls.protocol or "state" not in item:
            message = f"unsupported durable mount record protocol {protocol}"
            raise MessageRootStateError(message)
        address = _object(item.get("address"))
        values: dict[str, str | int] = {}
        for key, value in _object(address.get("values")).items():
            if not isinstance(key, str) or not isinstance(value, str | int):
                message = "mount address values must contain string keys and string or integer values"
                raise MessageRootStateError(message)
            values[key] = value
        expires_at = item.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, int | float):
            message = "mount record expires_at must be a number or null"
            raise MessageRootStateError(message)
        state_payload = json.dumps(item.get("state"), ensure_ascii=False, separators=(",", ":"))
        return DurableMessageRootRecord(
            protocol,
            MessageRootStateCodec.loads(state_payload),
            FrontendAddress(_string(address, "frontend"), values),
            float(expires_at) if expires_at is not None else None,
        )


class MessageRootStateCodec:
    """Canonical JSON codec for mount state protocol 1."""

    protocol = 1

    @classmethod
    def dumps(cls, state: MessageRootState) -> str:
        if state.protocol != cls.protocol:
            message = f"unsupported mount state protocol {state.protocol}"
            raise MessageRootStateError(message)
        raw = {
            "protocol": state.protocol,
            "component_key": state.component_key,
            "component_version": state.component_version,
            "components": [
                {"path": component.path, "type_id": component.type_id, "state": dict(component.state)}
                for component in state.components
            ],
            "presentation": _presentation_to_dict(state.presentation),
            "target": {
                "triple": state.target_triple,
                "version": state.target_version,
                "fingerprint": state.target_fingerprint,
                "adapter_capabilities": list(state.target_adapter_capabilities),
            },
        }
        try:
            return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            message = f"component state is not JSON serializable: {error}"
            raise MessageRootStateError(message) from error

    @classmethod
    def loads(cls, payload: str) -> MessageRootState:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise MessageRootStateError(str(error)) from error
        if not isinstance(raw, dict):
            message = "mount state must be an object"
            raise MessageRootStateError(message)
        protocol = _integer(raw, "protocol")
        if protocol != cls.protocol:
            message = f"unsupported mount state protocol {protocol}"
            raise MessageRootStateError(message)
        components = raw.get("components")
        presentation = raw.get("presentation")
        if not isinstance(components, list) or not isinstance(presentation, dict):
            message = "mount state components or presentation are malformed"
            raise MessageRootStateError(message)
        decoded_components: list[ComponentState] = []
        for value in components:
            item = _object(value)
            state = item.get("state")
            if not isinstance(state, dict) or not all(isinstance(key, str) for key in state):
                message = "component snapshot state must be an object with string keys"
                raise MessageRootStateError(message)
            decoded_components.append(
                ComponentState(
                    path=_string(item, "path"),
                    type_id=_string(item, "type_id"),
                    state=state,
                )
            )
        target = _object(raw.get("target"))
        adapter_capabilities = target.get("adapter_capabilities")
        if not isinstance(adapter_capabilities, list) or not all(
            isinstance(capability, str) for capability in adapter_capabilities
        ):
            message = "mount state target adapter_capabilities must be a string list"
            raise MessageRootStateError(message)
        if adapter_capabilities != sorted(set(adapter_capabilities)):
            message = "mount state target adapter_capabilities must be sorted and unique"
            raise MessageRootStateError(message)
        return MessageRootState(
            protocol,
            _string(raw, "component_key"),
            _integer(raw, "component_version"),
            tuple(decoded_components),
            _presentation_from_dict(presentation),
            _string(target, "triple"),
            _integer(target, "version"),
            _string(target, "fingerprint"),
            tuple(adapter_capabilities),
        )


def migrate_component_state(
    current: MessageRootState,
    path: str,
    transform: Callable[[Mapping[str, object]], Mapping[str, object]],
    *,
    type_id: str | None = None,
) -> MessageRootState:
    """Transform one component snapshot and advance the root's version by one."""
    isolated = MessageRootStateCodec.loads(MessageRootStateCodec.dumps(current))
    matches = [index for index, component in enumerate(isolated.components) if component.path == path]
    if len(matches) != 1:
        detail = "missing" if not matches else "duplicate"
        message = f"component path {path!r} is {detail} in the mount state"
        raise MessageRootStateError(message)
    if type_id == "":
        message = "component type identity must not be empty"
        raise MessageRootStateError(message)

    index = matches[0]
    component = current.components[index]
    transformed = transform(isolated.components[index].state)
    if not isinstance(transformed, Mapping) or not all(isinstance(key, str) for key in transformed):
        message = "component migration result must be a mapping with string keys"
        raise MessageRootStateError(message)
    components = list(current.components)
    components[index] = ComponentState(
        component.path,
        component.type_id if type_id is None else type_id,
        dict(transformed),
    )
    migrated = replace(
        current,
        component_version=current.component_version + 1,
        components=tuple(components),
    )
    MessageRootStateCodec.loads(MessageRootStateCodec.dumps(migrated))
    return migrated


def _presentation_to_dict(state: PresentationSnapshot) -> dict[str, object]:
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
            for key, cursor in state.cursors.items()
        },
        "selections": {key: {"selected": list(selection.selected)} for key, selection in state.selections.items()},
        "disclosures": {key: {"open": disclosure.open} for key, disclosure in state.disclosures.items()},
        "strategies": {
            key: {
                "node_key": strategy.node_key,
                "adapter_id": strategy.adapter_id,
                "adapter_version": strategy.adapter_version,
                "strategy_id": strategy.strategy_id,
            }
            for key, strategy in state.strategies.items()
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
    message_root_actor_id: int | None
    address: FrontendAddress
    expires_at: float | None
    parent_id: str | None


type MessageRootStateMigration = Callable[[MessageRootState], MessageRootState]


@dataclass(frozen=True, slots=True)
class _Registration:
    version: int
    factory: Callable[[], Component[Any]] | None
    restore: Callable[[RestoreContext], MessageRoot] | None
    migrations: Mapping[int, MessageRootStateMigration]


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
        restore: Callable[[RestoreContext], MessageRoot] | None = None,
        migrations: Mapping[int, MessageRootStateMigration] | None = None,
        factory: Callable[[], Component[Any]] | None = None,
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

    def capture(self, message_root: AnyMessageRoot, component_key: str) -> MessageRootState:
        registration = self._registrations.get(component_key)
        if registration is None:
            message = f"durable component {component_key!r} is not registered"
            raise MessageRootStateError(message)
        if not message_root.runtime.components:
            message = "a mount must be built before it can be snapshotted"
            raise MessageRootStateError(message)
        components = tuple(
            ComponentState(path, _type_id(component), export_state(component))
            for path, component in sorted(message_root.runtime.components.items())
        )
        snapshot = MessageRootState(
            MessageRootStateCodec.protocol,
            component_key,
            registration.version,
            components,
            PresentationSnapshot(
                dict(message_root.presentation.cursors),
                dict(message_root.presentation.selections),
                dict(message_root.presentation.disclosures),
                dict(message_root.presentation.strategies),
            ),
            message_root.target.triple,
            message_root.target.version,
            message_root.target.fingerprint,
            tuple(sorted(message_root.target.adapter_capabilities)),
        )
        MessageRootStateCodec.dumps(snapshot)
        return snapshot

    def restore(
        self,
        state: MessageRootState,
        context: RestoreContext | None = None,
        *,
        targets: TargetRegistry = DEFAULT_TARGETS,
        **message_root_options: Any,
    ) -> AnyMessageRoot:
        """Rebuild a mount from a state, against the exact target it was planned for.

        The target is resolved *before* anything is built, so an unavailable or changed
        profile fails while the record is still just data — not after a mount exists and a
        reader could already be clicking it.
        """
        target = targets.resolve(
            state.target_triple,
            state.target_version,
            state.target_fingerprint,
            state.target_adapter_capabilities,
        )
        message_root_options.setdefault("target", target)
        registration = self._registrations.get(state.component_key)
        if registration is None:
            message = f"durable component {state.component_key!r} is not registered"
            raise MessageRootStateError(message)
        state = self._migrate(state, registration)
        by_path = {component.path: component for component in state.components}
        if registration.restore is not None:
            if context is None:
                message = f"durable component {state.component_key!r} requires a restore context"
                raise MessageRootStateError(message)
            message_root = registration.restore(context)
            root = message_root.component
        else:
            factory = registration.factory
            if factory is None:
                message = f"durable component {state.component_key!r} has no restore recipe"
                raise MessageRootStateError(message)
            root = factory()
            message_root = MessageRoot(root, **message_root_options)
        root_snapshot = by_path.get("$")
        if root_snapshot is None:
            message = "mount state has no root component"
            raise MessageRootStateError(message)
        _restore_component(root, root_snapshot)
        tree = render_component_tree(root)
        if set(by_path) != set(tree.components):
            message = "restored component tree does not match the state paths"
            raise MessageRootStateError(message)
        for path, component in tree.components.items():
            if path != "$":
                _restore_component(component, by_path[path])
        message_root.presentation = PresentationState(
            cursors=dict(state.presentation.cursors),
            selections=dict(state.presentation.selections),
            disclosures=dict(state.presentation.disclosures),
            strategies=dict(state.presentation.strategies),
        )
        return message_root

    @staticmethod
    def _migrate(state: MessageRootState, registration: _Registration) -> MessageRootState:
        current = state
        while current.component_version < registration.version:
            migration = registration.migrations.get(current.component_version)
            if migration is None:
                message = (
                    f"durable component {current.component_key!r} has no migration from "
                    f"version {current.component_version}"
                )
                raise MessageRootStateError(message)
            source_version = current.component_version
            migrated = migration(current)
            if (
                migrated.protocol != MessageRootStateCodec.protocol
                or migrated.component_key != current.component_key
                or migrated.component_version != source_version + 1
            ):
                message = f"state migration from version {source_version} must produce version {source_version + 1}"
                raise MessageRootStateError(message)
            MessageRootStateCodec.dumps(migrated)
            current = MessageRootStateCodec.loads(MessageRootStateCodec.dumps(migrated))
        if current.component_version != registration.version:
            message = (
                f"durable component {current.component_key!r} state version "
                f"{current.component_version} is newer than registered version {registration.version}"
            )
            raise MessageRootStateError(message)
        return current


def _restore_component(component: AnyComponent, state: ComponentState) -> None:
    expected = _type_id(component)
    if state.type_id != expected:
        message = f"component at {state.path!r} changed type from {state.type_id!r} to {expected!r}"
        raise MessageRootStateError(message)
    try:
        restore_state(component, state.state)
    except ValueError as error:
        raise MessageRootStateError(str(error)) from error


def _type_id(component: Component[Any]) -> str:
    cls = type(component)
    return f"{cls.__module__}:{cls.__qualname__}"


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = "snapshot entry must be an object"
        raise MessageRootStateError(message)
    return value


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"snapshot field {key!r} must be a string"
        raise MessageRootStateError(message)
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"snapshot field {key!r} must be an integer"
        raise MessageRootStateError(message)
    return value


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        message = f"snapshot field {key!r} must be a string or null"
        raise MessageRootStateError(message)
    return value


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        message = f"snapshot field {key!r} must be a boolean"
        raise MessageRootStateError(message)
    return value


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        message = f"snapshot field {key!r} must be an array of strings"
        raise MessageRootStateError(message)
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
    DurableSessionCodec,
    DurableSessionRecord,
    SessionRootRecord,
    encode_session_scope,
)

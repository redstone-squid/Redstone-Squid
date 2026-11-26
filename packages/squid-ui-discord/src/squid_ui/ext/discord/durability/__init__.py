"""Restore live UI sessions onto their existing messages after a process restart.

ComponentRegistry names the recipes that rebuild components, callbacks, access policies,
and host dependencies. Snapshots save persistent component fields and presentation state,
such as the current page or selection; they do not serialize running Python objects.

DurableSessionRuntime coordinates the session store with a frontend that can reconnect
messages. DiscordFrontend records public message coordinates and replaces temporary
interaction-token access with bot-token editing. Register restore recipes before starting
recovery; DurableBot provides the login-before-recovery-before-gateway ordering.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Literal

from pydantic import ValidationError

from squid_reactivity import export_state, restore_state
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
from squid_ui.errors import SquidUiError
from squid_ui.ext.discord.durability._wire import (
    WireAddress,
    WireComponent,
    WireMount,
    WirePresentation,
    WireState,
    WireTarget,
)
from squid_ui.ext.discord.messages.root import AnyMessageRoot, MessageRoot
from squid_ui.ext.discord.sessions import SessionKey
from squid_ui.ext.discord.target import DISCORD_V2_DPY27
from squid_ui.ext.discord.targets import DEFAULT_TARGETS, TargetRegistry
from squid_ui.pages import PagePosition
from squid_ui.presentation_state import CursorState, NodeValue, PresentationState
from squid_ui.runtime.definition import AnyComponent
from squid_ui.runtime.expansion import render_component_tree

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


class MessageRootStateError(SquidUiError, ValueError):
    """A snapshot is malformed, incompatible, or unsafe to restore."""


@dataclass(frozen=True, slots=True)
class ComponentState:
    """Persistable state for one component at its stable path in a rendered tree."""

    path: str
    type_id: str
    state: Mapping[str, object]
    shared_with: str | None = None
    """Canonical placement holding this instance's state; aliases carry empty state."""


@dataclass(frozen=True, slots=True)
class PresentationSnapshot:
    """Saved pagination, runtime-owned node values, and declared cells for a message root."""

    cursors: Mapping[str, CursorState]
    node_values: Mapping[str, NodeValue]
    declared: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessageRootState:
    """A versioned component tree and presentation snapshot with its target identity.

    Callbacks, services, and access policy come from the registered restore recipe rather
    than this snapshot. Only component fields exported as persistent state are included.
    """

    protocol: int
    component_key: str
    component_version: int
    components: tuple[ComponentState, ...]
    presentation: PresentationSnapshot
    target_triple: str = DISCORD_V2_DPY27.triple
    target_version: int = DISCORD_V2_DPY27.version
    target_fingerprint: str = ""
    """Digest of the recorded target, including its limits.

    The same target name can be registered with different budgets. Recovery checks this
    fingerprint before reconstruction so saved state is not silently fitted to new limits.
    """
    target_adapter_capabilities: tuple[str, ...] = ()
    """Adapter capabilities recorded when the snapshot was captured."""


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

    protocol: ClassVar[Literal[1]] = 1

    @classmethod
    def dumps(cls, record: DurableMessageRootRecord) -> str:
        if record.protocol != cls.protocol:
            message = f"unsupported durable mount record protocol {record.protocol}"
            raise MessageRootStateError(message)
        try:
            wire = WireMount(
                protocol=cls.protocol,
                state=MessageRootStateCodec.to_wire(record.state),
                address=WireAddress(frontend=record.address.frontend, values=dict(record.address.values)),
                expires_at=record.expires_at,
            )
            return json.dumps(
                wire.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as error:
            message = f"durable mount metadata is not JSON serializable: {error}"
            raise MessageRootStateError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableMessageRootRecord:
        try:
            wire = WireMount.model_validate_json(payload)
        except ValidationError as error:
            raise MessageRootStateError(str(error)) from error
        return DurableMessageRootRecord(
            wire.protocol,
            MessageRootStateCodec.from_wire(wire.state),
            FrontendAddress(wire.address.frontend, wire.address.values),
            wire.expires_at,
        )


class MessageRootStateCodec:
    """Canonical JSON codec for mount state protocol 1."""

    protocol: ClassVar[Literal[1]] = 1

    @classmethod
    def dumps(cls, state: MessageRootState) -> str:
        if state.protocol != cls.protocol:
            message = f"unsupported mount state protocol {state.protocol}"
            raise MessageRootStateError(message)
        try:
            wire = cls.to_wire(state)
            return json.dumps(
                wire.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
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
        if raw.get("protocol") != cls.protocol:
            message = f"unsupported mount state protocol {raw.get('protocol')}"
            raise MessageRootStateError(message)
        try:
            return cls.from_wire(WireState.model_validate(raw))
        except ValidationError as error:
            raise MessageRootStateError(str(error)) from error

    @classmethod
    def to_wire(cls, state: MessageRootState) -> WireState:
        """Validate a snapshot and build its JSON contract without serializing it."""
        if state.protocol != cls.protocol:
            message = f"unsupported mount state protocol {state.protocol}"
            raise MessageRootStateError(message)
        return WireState(
            protocol=cls.protocol,
            component_key=state.component_key,
            component_version=state.component_version,
            components=[
                WireComponent(
                    path=component.path,
                    type_id=component.type_id,
                    state=dict(component.state),
                    shared_with=component.shared_with,
                )
                for component in state.components
            ],
            presentation=WirePresentation.model_validate(_presentation_to_dict(state.presentation)),
            target=WireTarget(
                triple=state.target_triple,
                version=state.target_version,
                fingerprint=state.target_fingerprint,
                adapter_capabilities=list(state.target_adapter_capabilities),
            ),
        )

    @classmethod
    def from_wire(cls, wire: WireState) -> MessageRootState:
        """Restore runtime snapshot values from a validated JSON contract."""
        components = tuple(
            ComponentState(item.path, item.type_id, item.state, item.shared_with) for item in wire.components
        )
        _validate_component_aliases(components)
        adapter_capabilities = wire.target.adapter_capabilities
        if adapter_capabilities != sorted(set(adapter_capabilities)):
            message = "mount state target adapter_capabilities must be sorted and unique"
            raise MessageRootStateError(message)
        return MessageRootState(
            wire.protocol,
            wire.component_key,
            wire.component_version,
            components,
            _presentation_from_wire(wire.presentation),
            wire.target.triple,
            wire.target.version,
            wire.target.fingerprint,
            tuple(adapter_capabilities),
        )


def _validate_component_aliases(components: Sequence[ComponentState]) -> None:
    by_path = {component.path: component for component in components}
    if len(by_path) != len(components):
        message = "duplicate component paths in mount state"
        raise MessageRootStateError(message)
    for component in components:
        if component.shared_with is None:
            continue
        canonical = by_path.get(component.shared_with)
        if (
            canonical is None
            or canonical.shared_with is not None
            or canonical.path == component.path
            or canonical.type_id != component.type_id
            or component.state
        ):
            message = f"invalid shared component placement {component.path!r}"
            raise MessageRootStateError(message)


def migrate_component_state(
    current: MessageRootState,
    path: str,
    transform: Callable[[Mapping[str, object]], Mapping[str, object]],
    *,
    type_id: str | None = None,
) -> MessageRootState:
    """Transform an instance's saved state through any placement and advance the root version."""
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
    if (canonical_path := current.components[index].shared_with) is not None:
        index = next(i for i, item in enumerate(current.components) if item.path == canonical_path)
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
    if type_id is not None:
        components = [
            replace(item, type_id=type_id) if item.shared_with == component.path else item for item in components
        ]
    migrated = replace(
        current,
        component_version=current.component_version + 1,
        components=tuple(components),
    )
    MessageRootStateCodec.loads(MessageRootStateCodec.dumps(migrated))
    return migrated


def _presentation_to_dict(state: PresentationSnapshot) -> dict[str, object]:
    # Custom values must pass through a registered codec, not Pydantic's implicit conversion.
    json.dumps(state.declared, allow_nan=False)
    return {
        "declared_protocol": 1,
        "declared": dict(state.declared),
        "cursors": {
            key: {
                "position": {
                    "anchor": cursor.position.anchor,
                    "offset": cursor.position.page,
                    "direction": "around",
                },
                "extent": cursor.extent,
                "fingerprint": cursor.fingerprint,
            }
            for key, cursor in state.cursors.items()
        },
        "node_values": {
            key: list(value) if isinstance(value, tuple) else value for key, value in state.node_values.items()
        },
    }


def _presentation_from_wire(wire: WirePresentation) -> PresentationSnapshot:
    return PresentationSnapshot(
        cursors={
            key: CursorState(
                PagePosition(item.position.anchor, item.position.offset),
                item.extent,
                item.fingerprint,
            )
            for key, item in wire.cursors.items()
        },
        node_values={
            key: tuple(value) if isinstance(value, list) else value for key, value in wire.node_values.items()
        },
        declared=wire.declared,
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
type PresentationTransform = Callable[[str, str, Any], Any]


@dataclass(frozen=True, slots=True)
class _Registration:
    version: int
    factory: Callable[[], AnyComponent] | None
    restore: Callable[[RestoreContext], MessageRoot] | None
    migrations: Mapping[int, MessageRootStateMigration]
    presentation_encode: PresentationTransform | None
    presentation_decode: PresentationTransform | None


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
        factory: Callable[[], AnyComponent] | None = None,
        presentation_encode: PresentationTransform | None = None,
        presentation_decode: PresentationTransform | None = None,
    ) -> None:
        """Register a root recipe and migrations indexed by their source version.

        Use ``restore`` for durable sessions: it builds the complete message root with its
        access policy and host dependencies from RestoreContext. ``factory`` builds only a
        component, for callers that supply message-root options separately. Supply one of
        these recipes. Missing migration steps fail when an older snapshot needs them.
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
        if (presentation_encode is None) != (presentation_decode is None):
            msg = "register presentation encode and decode together"
            raise ValueError(msg)
        if any(source < 1 or source >= version for source in migration_map):
            message = "snapshot migration sources must be positive versions below the registered version"
            raise ValueError(message)
        self._registrations[key] = _Registration(
            version, factory, restore, migration_map, presentation_encode, presentation_decode
        )

    def capture(self, message_root: AnyMessageRoot, component_key: str) -> MessageRootState:
        registration = self._registrations.get(component_key)
        if registration is None:
            message = f"durable component {component_key!r} is not registered"
            raise MessageRootStateError(message)
        if not message_root.runtime.components:
            message = "a mount must be built before it can be snapshotted"
            raise MessageRootStateError(message)
        canonical_paths: dict[AnyComponent, str] = {}
        components: list[ComponentState] = []
        for path, component in sorted(message_root.runtime.components.items()):
            canonical = canonical_paths.get(component)
            components.append(
                ComponentState(
                    path, _type_id(component), export_state(component) if canonical is None else {}, canonical
                )
            )
            canonical_paths.setdefault(component, path)
        snapshot = MessageRootState(
            MessageRootStateCodec.protocol,
            component_key,
            registration.version,
            tuple(components),
            PresentationSnapshot(
                dict(message_root.presentation.cursors),
                dict(message_root.presentation.node_values),
                {
                    path: {
                        name: value
                        if registration.presentation_encode is None
                        else registration.presentation_encode(path, name, value)
                        for name, value in fields.items()
                    }
                    for path, fields in message_root.runtime.presentation_cells.export(
                        message_root.runtime.components
                    ).items()
                },
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
        """Resolve the recorded target, migrate state, and reconstruct a mount without sending it.

        The reconstructed tree must match component paths, types, and shared instance relationships. The
        host owns access policy and services through its recipe. Custom restore callbacks and
        explicit target overrides must return a mount configured for the resolved target;
        this method does not recheck the returned mount's target.
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
        _validate_component_aliases(state.components)
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
        message_root.presentation = PresentationState(
            cursors=dict(state.presentation.cursors),
            node_values=dict(state.presentation.node_values),
        )
        message_root.runtime.presentation_cells.restore(
            {
                path: {
                    name: value
                    if registration.presentation_decode is None
                    else registration.presentation_decode(path, name, value)
                    for name, value in fields.items()
                }
                for path, fields in state.presentation.declared.items()
            }
        )
        tree = render_component_tree(root, runtime=message_root.runtime)
        if set(by_path) != set(tree.components):
            message = "restored component tree does not match the state paths"
            raise MessageRootStateError(message)
        canonical_instances: set[AnyComponent] = set()
        for path, component in tree.components.items():
            snapshot = by_path[path]
            if snapshot.shared_with is not None:
                if tree.components[snapshot.shared_with] is not component:
                    message = f"restored placement {path!r} does not share its saved component instance"
                    raise MessageRootStateError(message)
            else:
                if component in canonical_instances:
                    message = f"restored placement {path!r} unexpectedly shares a component instance"
                    raise MessageRootStateError(message)
                canonical_instances.add(component)
                if path != "$":
                    _restore_component(component, snapshot)
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


def _type_id(component: AnyComponent) -> str:
    """Return the stable module-qualified identity of a component class."""
    cls = type(component)
    return f"{cls.__module__}:{cls.__qualname__}"


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

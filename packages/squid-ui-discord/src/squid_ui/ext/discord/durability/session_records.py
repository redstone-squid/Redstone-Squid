"""Store a session and its tree of message roots as one versioned JSON record.

Each root record contains component state, message coordinates, and its parent ID. The
root appears first and every parent precedes its children, so recovery can rebuild the
attachment relationships. Membership, capacity, and quota remain session-wide facts.
"""

import json
import math
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import ValidationError

from squid_ui.ext.discord.sessions import (
    CustomScope,
    GlobalScope,
    GuildScope,
    SessionKey,
    UserGuildScope,
    UserScope,
)

from . import FrontendAddress, MessageRootState, MessageRootStateCodec, MessageRootStateError
from ._wire import (
    WireAddress,
    WireCustomScope,
    WireGlobalScope,
    WireGuildScope,
    WireSession,
    WireSessionKey,
    WireSessionRoot,
    WireUserGuildScope,
    WireUserScope,
)


@dataclass(frozen=True, slots=True)
class SessionRootRecord:
    """One stored mount and its position in a durable session graph."""

    id: str
    state: MessageRootState
    address: FrontendAddress
    parent_id: str | None
    actor_id: int | None


@dataclass(frozen=True, slots=True)
class DurableSessionRecord:
    """Every recoverable fact owned by one logical session record."""

    protocol: int
    id: str
    key: SessionKey
    actor_id: int | None
    opened_at: float
    expires_at: float | None
    message_roots: tuple[SessionRootRecord, ...]
    members: frozenset[int] = frozenset()
    capacity: int | None = None
    quota: int | None = None
    domain: str | None = None


class DurableSessionCodec:
    """Canonical JSON codec for durable session record protocol 1."""

    protocol: ClassVar[Literal[1]] = 1

    @classmethod
    def dumps(cls, record: DurableSessionRecord) -> str:
        cls._validate(record)
        try:
            wire = WireSession(
                protocol=cls.protocol,
                id=record.id,
                key=WireSessionKey.model_validate(encode_session_key(record.key)),
                actor_id=record.actor_id,
                opened_at=record.opened_at,
                expires_at=record.expires_at,
                members=sorted(record.members),
                capacity=record.capacity,
                quota=record.quota,
                domain=record.domain,
                message_roots=[
                    WireSessionRoot(
                        id=root.id,
                        state=MessageRootStateCodec.to_wire(root.state),
                        address=WireAddress(frontend=root.address.frontend, values=dict(root.address.values)),
                        parent_id=root.parent_id,
                        actor_id=root.actor_id,
                    )
                    for root in record.message_roots
                ],
            )
            return json.dumps(
                wire.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as error:
            message = f"durable session record is not JSON serializable: {error}"
            raise MessageRootStateError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableSessionRecord:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise MessageRootStateError(str(error)) from error
        if not isinstance(raw, dict):
            message = "durable session record must be an object"
            raise MessageRootStateError(message)
        if raw.get("protocol") != cls.protocol:
            message = f"unsupported durable session record protocol {raw.get('protocol')}"
            raise MessageRootStateError(message)
        try:
            wire = WireSession.model_validate(raw)
        except ValidationError as error:
            raise MessageRootStateError(str(error)) from error
        record = DurableSessionRecord(
            protocol=wire.protocol,
            id=wire.id,
            key=decode_session_key(wire.key),
            actor_id=wire.actor_id,
            opened_at=wire.opened_at,
            expires_at=wire.expires_at,
            message_roots=tuple(
                SessionRootRecord(
                    id=root.id,
                    state=MessageRootStateCodec.from_wire(root.state),
                    address=FrontendAddress(root.address.frontend, root.address.values),
                    parent_id=root.parent_id,
                    actor_id=root.actor_id,
                )
                for root in wire.message_roots
            ),
            members=frozenset(wire.members),
            capacity=wire.capacity,
            quota=wire.quota,
            domain=wire.domain,
        )
        cls._validate(record)
        return record

    @classmethod
    def _validate(cls, record: DurableSessionRecord) -> None:
        if record.protocol != cls.protocol:
            message = f"unsupported durable session record protocol {record.protocol}"
            raise MessageRootStateError(message)
        if not record.id or not record.message_roots:
            message = "durable sessions require a non-empty id and at least one mount"
            raise MessageRootStateError(message)
        ids = {message_root.id for message_root in record.message_roots}
        if len(ids) != len(record.message_roots) or "" in ids:
            message = "durable mount ids must be non-empty and unique"
            raise MessageRootStateError(message)
        roots = tuple(message_root for message_root in record.message_roots if message_root.parent_id is None)
        if len(roots) != 1 or record.message_roots[0] is not roots[0]:
            message = "durable sessions require exactly one root mount in the first position"
            raise MessageRootStateError(message)
        if any(
            message_root.parent_id is not None and message_root.parent_id not in ids
            for message_root in record.message_roots
        ):
            message = "durable mount parent does not exist in the same record"
            raise MessageRootStateError(message)
        preceding: set[str] = set()
        for message_root in record.message_roots:
            if message_root.parent_id is not None and message_root.parent_id not in preceding:
                message = "durable mount parents must precede their children"
                raise MessageRootStateError(message)
            preceding.add(message_root.id)
        for message_root in record.message_roots:
            seen = {message_root.id}
            parent_id = message_root.parent_id
            while parent_id is not None:
                if parent_id in seen:
                    message = "durable mount graph contains a cycle"
                    raise MessageRootStateError(message)
                seen.add(parent_id)
                parent = next(candidate for candidate in record.message_roots if candidate.id == parent_id)
                parent_id = parent.parent_id
        encode_session_key(record.key)


def encode_session_key(key: SessionKey) -> dict[str, object]:
    """Return the canonical JSON object used for storage scope and record payloads."""
    scope = key.scope
    if isinstance(scope, UserScope):
        encoded = WireUserScope(user_id=scope.user_id)
    elif isinstance(scope, GuildScope):
        encoded = WireGuildScope(guild_id=scope.guild_id)
    elif isinstance(scope, UserGuildScope):
        encoded = WireUserGuildScope(user_id=scope.user_id, guild_id=scope.guild_id)
    elif isinstance(scope, GlobalScope):
        encoded = WireGlobalScope()
    elif isinstance(scope, CustomScope):
        encoded = WireCustomScope(value=_encode_custom_scope(scope.value))
    else:
        message = f"unsupported durable session scope {type(scope).__name__}"
        raise MessageRootStateError(message)
    try:
        return WireSessionKey(name=key.name, scope=encoded).model_dump(mode="json")
    except ValidationError as error:
        raise MessageRootStateError(str(error)) from error


def decode_session_key(raw: Mapping[str, object] | WireSessionKey) -> SessionKey:
    """Decode a session key produced by :func:`encode_session_key`."""
    try:
        key = raw if isinstance(raw, WireSessionKey) else WireSessionKey.model_validate(raw)
    except ValidationError as error:
        raise MessageRootStateError(str(error)) from error
    match key.scope:
        case WireUserScope(user_id=user_id):
            return SessionKey.user(key.name, user_id)
        case WireGuildScope(guild_id=guild_id):
            return SessionKey.guild(key.name, guild_id)
        case WireUserGuildScope(user_id=user_id, guild_id=guild_id):
            return SessionKey.user_guild(key.name, user_id, guild_id)
        case WireGlobalScope():
            return SessionKey.global_(key.name)
        case WireCustomScope(value=value):
            return SessionKey.custom(key.name, _decode_custom_scope(value))


def encode_session_scope(key: SessionKey) -> str:
    """Return the canonical store index for a durable logical session key."""
    return json.dumps(encode_session_key(key), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_custom_scope(value: object) -> object:
    """Encode the supported hashable scope vocabulary as JSON values."""
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value):
            message = "durable custom scope numbers must be finite"
            raise MessageRootStateError(message)
        return value
    if isinstance(value, tuple):
        return [_encode_custom_scope(item) for item in value]
    message = "durable custom scopes support only JSON scalars and nested tuples"
    raise MessageRootStateError(message)


def _decode_custom_scope(value: object) -> Hashable:
    """Decode JSON values into the immutable custom-scope vocabulary."""
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return tuple(_decode_custom_scope(item) for item in value)
    message = "durable custom scope value is malformed"
    raise MessageRootStateError(message)

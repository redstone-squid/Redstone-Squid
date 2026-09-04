"""Canonical records for durable logical sessions and their mount graphs."""

import json
import math
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from squid_ui_discord.sessions import (
    CustomScope,
    GlobalScope,
    GuildScope,
    SessionKey,
    UserGuildScope,
    UserScope,
)

from . import FrontendAddress, MessageRootState, MessageRootStateCodec, MessageRootStateError
from ._json import require_integer, require_number, require_object, require_string


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

    protocol = 1

    @classmethod
    def dumps(cls, record: DurableSessionRecord) -> str:
        cls._validate(record)
        raw = {
            "protocol": record.protocol,
            "id": record.id,
            "key": encode_session_key(record.key),
            "actor_id": record.actor_id,
            "opened_at": record.opened_at,
            "expires_at": record.expires_at,
            "members": sorted(record.members),
            "capacity": record.capacity,
            "quota": record.quota,
            "domain": record.domain,
            "message_roots": [
                {
                    "id": message_root.id,
                    "state": json.loads(MessageRootStateCodec.dumps(message_root.state)),
                    "address": {"frontend": message_root.address.frontend, "values": dict(message_root.address.values)},
                    "parent_id": message_root.parent_id,
                    "actor_id": message_root.actor_id,
                }
                for message_root in record.message_roots
            ],
        }
        try:
            return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            message = f"durable session record is not JSON serializable: {error}"
            raise MessageRootStateError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableSessionRecord:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise MessageRootStateError(str(error)) from error
        item = require_object(raw, "durable session record")
        protocol = require_integer(item, "protocol", description="session record field")
        if protocol != cls.protocol:
            message = f"unsupported durable session record protocol {protocol}"
            raise MessageRootStateError(message)
        raw_roots = item.get("message_roots")
        if not isinstance(raw_roots, list):
            message = "durable session message_roots must be an array"
            raise MessageRootStateError(message)
        message_roots: list[SessionRootRecord] = []
        for raw_root in raw_roots:
            message_root = require_object(raw_root, "durable mount")
            address = require_object(message_root.get("address"), "mount address")
            values = require_object(address.get("values"), "mount address values")
            if not all(isinstance(key, str) and isinstance(value, str | int) for key, value in values.items()):
                message = "mount address values must contain string keys and string or integer values"
                raise MessageRootStateError(message)
            actor_id = message_root.get("actor_id")
            if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
                message = "mount actor_id must be an integer or null"
                raise MessageRootStateError(message)
            parent_id = message_root.get("parent_id")
            if parent_id is not None and not isinstance(parent_id, str):
                message = "mount parent_id must be a string or null"
                raise MessageRootStateError(message)
            message_roots.append(
                SessionRootRecord(
                    id=require_string(message_root, "id", description="mount field"),
                    state=MessageRootStateCodec.loads(
                        json.dumps(message_root.get("state"), ensure_ascii=False, separators=(",", ":"))
                    ),
                    address=FrontendAddress(
                        require_string(address, "frontend", description="mount address field"), values
                    ),
                    parent_id=parent_id,
                    actor_id=actor_id,
                )
            )
        actor_id = item.get("actor_id")
        if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
            message = "session actor_id must be an integer or null"
            raise MessageRootStateError(message)
        opened_at = require_number(item, "opened_at", description="session record field")
        expires_at = item.get("expires_at")
        if expires_at is not None and (
            not isinstance(expires_at, int | float) or isinstance(expires_at, bool) or not math.isfinite(expires_at)
        ):
            message = "session expires_at must be a number or null"
            raise MessageRootStateError(message)
        record = DurableSessionRecord(
            protocol=protocol,
            id=require_string(item, "id", description="session record field"),
            key=decode_session_key(require_object(item.get("key"), "session key")),
            actor_id=actor_id,
            opened_at=opened_at,
            expires_at=None if expires_at is None else float(expires_at),
            message_roots=tuple(message_roots),
            members=_member_ids(item.get("members")),
            capacity=_capacity(item.get("capacity")),
            quota=_capacity(item.get("quota")),
            domain=_domain(item.get("domain")),
        )
        cls._validate(record)
        return record

    @classmethod
    def _validate(cls, record: DurableSessionRecord) -> None:
        if record.protocol != cls.protocol:
            message = f"unsupported durable session record protocol {record.protocol}"
            raise MessageRootStateError(message)
        _member_ids(sorted(record.members))
        _capacity(record.capacity)
        _capacity(record.quota)
        _domain(record.domain)
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


class SessionScopeKind(StrEnum):
    USER = "user"
    GUILD = "guild"
    USER_GUILD = "user_guild"
    GLOBAL = "global"
    CUSTOM = "custom"


def encode_session_key(key: SessionKey) -> dict[str, object]:
    """Return the canonical JSON object used for storage scope and record payloads."""
    scope = key.scope
    if isinstance(scope, UserScope):
        encoded: dict[str, object] = {"type": SessionScopeKind.USER, "user_id": scope.user_id}
    elif isinstance(scope, GuildScope):
        encoded = {"type": SessionScopeKind.GUILD, "guild_id": scope.guild_id}
    elif isinstance(scope, UserGuildScope):
        encoded = {"type": SessionScopeKind.USER_GUILD, "user_id": scope.user_id, "guild_id": scope.guild_id}
    elif isinstance(scope, GlobalScope):
        encoded = {"type": SessionScopeKind.GLOBAL}
    elif isinstance(scope, CustomScope):
        encoded = {"type": SessionScopeKind.CUSTOM, "value": _encode_custom_scope(scope.value)}
    else:
        message = f"unsupported durable session scope {type(scope).__name__}"
        raise MessageRootStateError(message)
    if not key.name:
        message = "durable session key names must be non-empty"
        raise MessageRootStateError(message)
    return {"name": key.name, "scope": encoded}


def decode_session_key(raw: Mapping[str, object]) -> SessionKey:
    """Decode a session key produced by :func:`encode_session_key`."""
    name = require_string(raw, "name", description="session key field")
    scope = require_object(raw.get("scope"), "session scope")
    kind = require_string(scope, "type", description="session scope field")
    if kind == SessionScopeKind.USER:
        return SessionKey.user(name, require_integer(scope, "user_id", description="session scope field"))
    if kind == SessionScopeKind.GUILD:
        return SessionKey.guild(name, require_integer(scope, "guild_id", description="session scope field"))
    if kind == SessionScopeKind.USER_GUILD:
        return SessionKey.user_guild(
            name,
            require_integer(scope, "user_id", description="session scope field"),
            require_integer(scope, "guild_id", description="session scope field"),
        )
    if kind == SessionScopeKind.GLOBAL:
        return SessionKey.global_(name)
    if kind == SessionScopeKind.CUSTOM:
        return SessionKey.custom(name, _decode_custom_scope(scope.get("value")))
    message = f"unsupported durable session scope type {kind!r}"
    raise MessageRootStateError(message)


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


def _member_ids(value: object) -> frozenset[int]:
    """Narrow a stored member list to positive Discord snowflakes."""
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in value
    ):
        message = "durable session members must be an array of positive integers"
        raise MessageRootStateError(message)
    return frozenset(value)


def _capacity(value: object) -> int | None:
    """Narrow an optional stored capacity to a positive integer."""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        message = "durable session capacity must be a positive integer or null"
        raise MessageRootStateError(message)
    return value


def _domain(value: object) -> str | None:
    """Narrow an optional stored quota domain to a non-empty string."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        message = "durable session domain must be a non-empty string or null"
        raise MessageRootStateError(message)
    return value

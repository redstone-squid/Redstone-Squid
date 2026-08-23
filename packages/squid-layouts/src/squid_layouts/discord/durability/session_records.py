"""Canonical records for durable logical sessions and their mount graphs."""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from squid_layouts.discord.sessions import (
    CustomScope,
    GlobalScope,
    GuildScope,
    SessionKey,
    UserGuildScope,
    UserScope,
)

from . import MountLocator, MountSnapshot, SnapshotCodec, SnapshotError


@dataclass(frozen=True, slots=True)
class DurableMountState:
    """One snapshotted mount and its position in a durable session graph."""

    id: str
    snapshot: MountSnapshot
    locator: MountLocator
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
    mounts: tuple[DurableMountState, ...]


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
            "mounts": [
                {
                    "id": mount.id,
                    "snapshot": json.loads(SnapshotCodec.dumps(mount.snapshot)),
                    "locator": {"frontend": mount.locator.frontend, "values": dict(mount.locator.values)},
                    "parent_id": mount.parent_id,
                    "actor_id": mount.actor_id,
                }
                for mount in record.mounts
            ],
        }
        try:
            return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            message = f"durable session record is not JSON serializable: {error}"
            raise SnapshotError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableSessionRecord:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SnapshotError(str(error)) from error
        item = _object(raw, "durable session record")
        protocol = _integer(item, "protocol")
        if protocol != cls.protocol:
            message = f"unsupported durable session record protocol {protocol}"
            raise SnapshotError(message)
        raw_mounts = item.get("mounts")
        if not isinstance(raw_mounts, list):
            message = "durable session mounts must be an array"
            raise SnapshotError(message)
        mounts: list[DurableMountState] = []
        for raw_mount in raw_mounts:
            mount = _object(raw_mount, "durable mount")
            locator = _object(mount.get("locator"), "mount locator")
            values = _object(locator.get("values"), "mount locator values")
            if not all(isinstance(key, str) and isinstance(value, str | int) for key, value in values.items()):
                message = "mount locator values must contain string keys and string or integer values"
                raise SnapshotError(message)
            actor_id = mount.get("actor_id")
            if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
                message = "mount actor_id must be an integer or null"
                raise SnapshotError(message)
            parent_id = mount.get("parent_id")
            if parent_id is not None and not isinstance(parent_id, str):
                message = "mount parent_id must be a string or null"
                raise SnapshotError(message)
            mounts.append(
                DurableMountState(
                    id=_string(mount, "id"),
                    snapshot=SnapshotCodec.loads(
                        json.dumps(mount.get("snapshot"), ensure_ascii=False, separators=(",", ":"))
                    ),
                    locator=MountLocator(_string(locator, "frontend"), values),
                    parent_id=parent_id,
                    actor_id=actor_id,
                )
            )
        actor_id = item.get("actor_id")
        if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
            message = "session actor_id must be an integer or null"
            raise SnapshotError(message)
        opened_at = _number(item, "opened_at")
        expires_at = item.get("expires_at")
        if expires_at is not None and (
            not isinstance(expires_at, int | float) or isinstance(expires_at, bool) or not math.isfinite(expires_at)
        ):
            message = "session expires_at must be a number or null"
            raise SnapshotError(message)
        record = DurableSessionRecord(
            protocol=protocol,
            id=_string(item, "id"),
            key=decode_session_key(_object(item.get("key"), "session key")),
            actor_id=actor_id,
            opened_at=opened_at,
            expires_at=None if expires_at is None else float(expires_at),
            mounts=tuple(mounts),
        )
        cls._validate(record)
        return record

    @classmethod
    def _validate(cls, record: DurableSessionRecord) -> None:
        if record.protocol != cls.protocol:
            message = f"unsupported durable session record protocol {record.protocol}"
            raise SnapshotError(message)
        if not record.id or not record.mounts:
            message = "durable sessions require a non-empty id and at least one mount"
            raise SnapshotError(message)
        ids = {mount.id for mount in record.mounts}
        if len(ids) != len(record.mounts) or "" in ids:
            message = "durable mount ids must be non-empty and unique"
            raise SnapshotError(message)
        roots = tuple(mount for mount in record.mounts if mount.parent_id is None)
        if len(roots) != 1 or record.mounts[0] is not roots[0]:
            message = "durable sessions require exactly one root mount in the first position"
            raise SnapshotError(message)
        if any(mount.parent_id is not None and mount.parent_id not in ids for mount in record.mounts):
            message = "durable mount parent does not exist in the same record"
            raise SnapshotError(message)
        preceding: set[str] = set()
        for mount in record.mounts:
            if mount.parent_id is not None and mount.parent_id not in preceding:
                message = "durable mount parents must precede their children"
                raise SnapshotError(message)
            preceding.add(mount.id)
        for mount in record.mounts:
            seen = {mount.id}
            parent_id = mount.parent_id
            while parent_id is not None:
                if parent_id in seen:
                    message = "durable mount graph contains a cycle"
                    raise SnapshotError(message)
                seen.add(parent_id)
                parent = next(candidate for candidate in record.mounts if candidate.id == parent_id)
                parent_id = parent.parent_id
        encode_session_key(record.key)


class SessionScopeKind(StrEnum):
    USER = "user"
    GUILD = "guild"
    USER_GUILD = "user_guild"
    GLOBAL = "global"
    CUSTOM = "custom"


def encode_session_key(key: SessionKey) -> dict[str, Any]:
    """Return the canonical JSON object used for storage scope and record payloads."""
    scope = key.scope
    if isinstance(scope, UserScope):
        encoded = {"type": SessionScopeKind.USER, "user_id": scope.user_id}
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
        raise SnapshotError(message)
    if not key.name:
        message = "durable session key names must be non-empty"
        raise SnapshotError(message)
    return {"name": key.name, "scope": encoded}


def decode_session_key(raw: dict[str, Any]) -> SessionKey:
    """Decode a session key previously produced by :func:`encode_session_key`."""
    name = _string(raw, "name")
    scope = _object(raw.get("scope"), "session scope")
    kind = _string(scope, "type")
    if kind == SessionScopeKind.USER:
        return SessionKey.user(name, _integer(scope, "user_id"))
    if kind == SessionScopeKind.GUILD:
        return SessionKey.guild(name, _integer(scope, "guild_id"))
    if kind == SessionScopeKind.USER_GUILD:
        return SessionKey.user_guild(name, _integer(scope, "user_id"), _integer(scope, "guild_id"))
    if kind == SessionScopeKind.GLOBAL:
        return SessionKey.global_(name)
    if kind == SessionScopeKind.CUSTOM:
        return SessionKey.custom(name, _decode_custom_scope(scope.get("value")))
    message = f"unsupported durable session scope type {kind!r}"
    raise SnapshotError(message)


def encode_session_scope(key: SessionKey) -> str:
    """Return the canonical store index for a durable logical session key."""
    return json.dumps(encode_session_key(key), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_custom_scope(value: Any) -> Any:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        json.dumps(value, allow_nan=False)
        return value
    if isinstance(value, tuple):
        return [_encode_custom_scope(item) for item in value]
    message = "durable custom scopes support only JSON scalars and nested tuples"
    raise SnapshotError(message)


def _decode_custom_scope(value: Any) -> Any:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        return tuple(_decode_custom_scope(item) for item in value)
    message = "durable custom scope value is malformed"
    raise SnapshotError(message)


def _object(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{description} must be an object with string keys"
        raise SnapshotError(message)
    return value


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"{key} must be a string"
        raise SnapshotError(message)
    return value


def _integer(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{key} must be an integer"
        raise SnapshotError(message)
    return value


def _number(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        message = f"{key} must be a number"
        raise SnapshotError(message)
    return float(value)

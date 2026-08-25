"""Canonical records for durable logical sessions and their mount graphs."""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from squid_discord.sessions import (
    CustomScope,
    GlobalScope,
    GuildScope,
    SessionKey,
    UserGuildScope,
    UserScope,
)

from . import MountLocator, MountState, MountStateCodec, MountStateError


@dataclass(frozen=True, slots=True)
class SessionMountRecord:
    """One stored mount and its position in a durable session graph."""

    id: str
    state: MountState
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
    mounts: tuple[SessionMountRecord, ...]
    members: frozenset[int] = frozenset()
    capacity: int | None = None
    quota: int | None = None
    domain: str | None = None


class DurableSessionCodec:
    """Canonical JSON codec for durable session records.

    Protocol 2 adds explicit membership. Protocol 1 records predate it and decode with an
    unbounded capacity and the stored opener as their only member, which is what they meant.
    """

    protocol = 2
    supported = (1, 2)

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
            "mounts": [
                {
                    "id": mount.id,
                    # Wire key stays "snapshot"; only the Python field was renamed.
                    "snapshot": json.loads(MountStateCodec.dumps(mount.state)),
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
            raise MountStateError(message) from error

    @classmethod
    def loads(cls, payload: str) -> DurableSessionRecord:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise MountStateError(str(error)) from error
        item = _object(raw, "durable session record")
        protocol = _integer(item, "protocol")
        if protocol not in cls.supported:
            message = f"unsupported durable session record protocol {protocol}"
            raise MountStateError(message)
        raw_mounts = item.get("mounts")
        if not isinstance(raw_mounts, list):
            message = "durable session mounts must be an array"
            raise MountStateError(message)
        mounts: list[SessionMountRecord] = []
        for raw_mount in raw_mounts:
            mount = _object(raw_mount, "durable mount")
            locator = _object(mount.get("locator"), "mount locator")
            values = _object(locator.get("values"), "mount locator values")
            if not all(isinstance(key, str) and isinstance(value, str | int) for key, value in values.items()):
                message = "mount locator values must contain string keys and string or integer values"
                raise MountStateError(message)
            actor_id = mount.get("actor_id")
            if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
                message = "mount actor_id must be an integer or null"
                raise MountStateError(message)
            parent_id = mount.get("parent_id")
            if parent_id is not None and not isinstance(parent_id, str):
                message = "mount parent_id must be a string or null"
                raise MountStateError(message)
            mounts.append(
                SessionMountRecord(
                    id=_string(mount, "id"),
                    state=MountStateCodec.loads(
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
            raise MountStateError(message)
        opened_at = _number(item, "opened_at")
        expires_at = item.get("expires_at")
        if expires_at is not None and (
            not isinstance(expires_at, int | float) or isinstance(expires_at, bool) or not math.isfinite(expires_at)
        ):
            message = "session expires_at must be a number or null"
            raise MountStateError(message)
        legacy_members = () if actor_id is None else (actor_id,)
        record = DurableSessionRecord(
            protocol=protocol,
            id=_string(item, "id"),
            key=decode_session_key(_object(item.get("key"), "session key")),
            actor_id=actor_id,
            opened_at=opened_at,
            expires_at=None if expires_at is None else float(expires_at),
            mounts=tuple(mounts),
            members=_member_ids(item.get("members", legacy_members)),
            capacity=_capacity(item.get("capacity")),
            quota=_capacity(item.get("quota")),
            domain=_domain(item.get("domain")),
        )
        cls._validate(record)
        return record

    @classmethod
    def _validate(cls, record: DurableSessionRecord) -> None:
        if record.protocol not in cls.supported:
            message = f"unsupported durable session record protocol {record.protocol}"
            raise MountStateError(message)
        _member_ids(sorted(record.members))
        _capacity(record.capacity)
        _capacity(record.quota)
        _domain(record.domain)
        if not record.id or not record.mounts:
            message = "durable sessions require a non-empty id and at least one mount"
            raise MountStateError(message)
        ids = {mount.id for mount in record.mounts}
        if len(ids) != len(record.mounts) or "" in ids:
            message = "durable mount ids must be non-empty and unique"
            raise MountStateError(message)
        roots = tuple(mount for mount in record.mounts if mount.parent_id is None)
        if len(roots) != 1 or record.mounts[0] is not roots[0]:
            message = "durable sessions require exactly one root mount in the first position"
            raise MountStateError(message)
        if any(mount.parent_id is not None and mount.parent_id not in ids for mount in record.mounts):
            message = "durable mount parent does not exist in the same record"
            raise MountStateError(message)
        preceding: set[str] = set()
        for mount in record.mounts:
            if mount.parent_id is not None and mount.parent_id not in preceding:
                message = "durable mount parents must precede their children"
                raise MountStateError(message)
            preceding.add(mount.id)
        for mount in record.mounts:
            seen = {mount.id}
            parent_id = mount.parent_id
            while parent_id is not None:
                if parent_id in seen:
                    message = "durable mount graph contains a cycle"
                    raise MountStateError(message)
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
        raise MountStateError(message)
    if not key.name:
        message = "durable session key names must be non-empty"
        raise MountStateError(message)
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
    raise MountStateError(message)


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
    raise MountStateError(message)


def _decode_custom_scope(value: Any) -> Any:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        return tuple(_decode_custom_scope(item) for item in value)
    message = "durable custom scope value is malformed"
    raise MountStateError(message)


def _object(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{description} must be an object with string keys"
        raise MountStateError(message)
    return value


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"{key} must be a string"
        raise MountStateError(message)
    return value


def _member_ids(value: object) -> frozenset[int]:
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in value
    ):
        message = "durable session members must be an array of positive integers"
        raise MountStateError(message)
    return frozenset(value)


def _capacity(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        message = "durable session capacity must be a positive integer or null"
        raise MountStateError(message)
    return value


def _domain(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        message = "durable session domain must be a non-empty string or null"
        raise MountStateError(message)
    return value


def _integer(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{key} must be an integer"
        raise MountStateError(message)
    return value


def _number(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        message = f"{key} must be a number"
        raise MountStateError(message)
    return float(value)

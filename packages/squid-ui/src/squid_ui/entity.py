"""Portable vocabulary and stable encoding for entity-selection controls."""

import base64
from dataclasses import dataclass
from enum import StrEnum


class EntityType(StrEnum):
    """Families of entity picker exposed by a frontend."""

    USER = "user"
    ROLE = "role"
    CONVERSATION = "conversation"
    MENTIONABLE = "mentionable"


class EntityKind(StrEnum):
    """Concrete entity kinds carried by selections and defaults."""

    USER = "user"
    ROLE = "role"
    CONVERSATION = "conversation"


class ConversationType(StrEnum):
    """Exact frontend conversation families accepted by a conversation picker."""

    GUILD_TEXT = "guild_text"
    GUILD_VOICE = "guild_voice"
    GUILD_CATEGORY = "guild_category"
    GUILD_ANNOUNCEMENT = "guild_announcement"
    GUILD_ANNOUNCEMENT_THREAD = "guild_announcement_thread"
    GUILD_PUBLIC_THREAD = "guild_public_thread"
    GUILD_PRIVATE_THREAD = "guild_private_thread"
    GUILD_STAGE_VOICE = "guild_stage_voice"
    GUILD_FORUM = "guild_forum"
    GUILD_MEDIA = "guild_media"
    WORKSPACE_PUBLIC = "workspace_public"
    WORKSPACE_PRIVATE = "workspace_private"
    DIRECT = "direct"
    GROUP_DIRECT = "group_direct"


@dataclass(frozen=True, slots=True)
class EntityRef:
    """A portable reference to one concrete entity."""

    kind: EntityKind
    id: int | str

    def __post_init__(self) -> None:
        if not isinstance(self.id, int | str):
            message = "entity ids must be integers or strings"
            raise TypeError(message)
        if isinstance(self.id, bool) or (isinstance(self.id, int) and self.id <= 0):
            message = "integer entity ids must be positive"
            raise ValueError(message)
        if isinstance(self.id, str) and not self.id:
            message = "string entity ids cannot be empty"
            raise ValueError(message)


def encode_entity_ref(ref: EntityRef) -> str:
    """Encode a reference without conflating integer and string identifiers."""
    if isinstance(ref.id, int):
        return f"{ref.kind.value}:i:{ref.id}"
    encoded = base64.urlsafe_b64encode(ref.id.encode()).decode().rstrip("=")
    return f"{ref.kind.value}:s:{encoded}"


def decode_entity_ref(value: str) -> EntityRef:
    """Decode one tagged presentation-state reference."""
    kind, separator, encoded = value.partition(":")
    if not separator:
        message = "entity reference is missing its kind separator"
        raise ValueError(message)
    if encoded.startswith("i:"):
        return EntityRef(EntityKind(kind), int(encoded[2:]))
    if encoded.startswith("s:"):
        raw = encoded[2:]
        padded = raw + "=" * (-len(raw) % 4)
        try:
            identifier = base64.urlsafe_b64decode(padded.encode()).decode()
        except (UnicodeDecodeError, ValueError) as error:
            message = "entity reference contains an invalid string identifier"
            raise ValueError(message) from error
        return EntityRef(EntityKind(kind), identifier)
    message = "entity reference identifier must use an i or s tag"
    raise ValueError(message)


def supports_entity(entity_type: EntityType, kind: EntityKind) -> bool:
    """Whether a picker family may contain a concrete entity kind."""
    return (entity_type is EntityType.MENTIONABLE and kind in {EntityKind.USER, EntityKind.ROLE}) or (
        entity_type.value == kind.value
    )


__all__ = [
    "ConversationType",
    "EntityKind",
    "EntityRef",
    "EntityType",
    "decode_entity_ref",
    "encode_entity_ref",
    "supports_entity",
]

"""Portable vocabulary for entity-selection controls."""

from dataclasses import dataclass
from enum import StrEnum


class EntityType(StrEnum):
    """Families of entity picker exposed by a frontend."""

    USER = "user"
    ROLE = "role"
    CHANNEL = "channel"
    MENTIONABLE = "mentionable"


class EntityKind(StrEnum):
    """Concrete entity kinds carried by selections and defaults."""

    USER = "user"
    ROLE = "role"
    CHANNEL = "channel"


class ChannelType(StrEnum):
    """Discord channel families accepted by a channel picker."""

    TEXT = "text"
    VOICE = "voice"
    CATEGORY = "category"
    ANNOUNCEMENT = "announcement"
    ANNOUNCEMENT_THREAD = "announcement_thread"
    PUBLIC_THREAD = "public_thread"
    PRIVATE_THREAD = "private_thread"
    STAGE_VOICE = "stage_voice"
    FORUM = "forum"
    MEDIA = "media"


@dataclass(frozen=True, slots=True)
class EntityRef:
    """A portable reference to one concrete entity."""

    kind: EntityKind
    id: int

    def __post_init__(self) -> None:
        if self.id <= 0:
            message = "entity ids must be positive"
            raise ValueError(message)


def supports_entity(entity_type: EntityType, kind: EntityKind) -> bool:
    """Whether a picker family may contain a concrete entity kind."""
    return (entity_type is EntityType.MENTIONABLE and kind in {EntityKind.USER, EntityKind.ROLE}) or (
        entity_type.value == kind.value
    )


__all__ = [
    "ChannelType",
    "EntityKind",
    "EntityRef",
    "EntityType",
    "supports_entity",
]

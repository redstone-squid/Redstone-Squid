"""Resolved control choices shared by target composition backends."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from squid_ui.action_mode import ActionMode
from squid_ui.content.emoji import Emoji
from squid_ui.content.entity import ConversationType, EntityRef, EntityType


@dataclass(frozen=True, slots=True)
class Option:
    label: str
    value: str
    description: str | None = None
    default: Annotated[bool, Field(strict=True)] = False
    emoji: Emoji | None = None


@dataclass(frozen=True, slots=True)
class Select:
    options: tuple[Option, ...]
    action: str
    placeholder: str | None = None
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedSelect:
    options: tuple[Option, ...]
    route_id: str
    placeholder: str | None = None
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class EntitySelect:
    entity_type: EntityType
    action: str
    placeholder: str | None = None
    default_values: tuple[EntityRef, ...] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE

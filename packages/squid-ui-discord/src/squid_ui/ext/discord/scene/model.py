"""Resolved Discord scene vocabulary."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pydantic import Field

from squid_ui.action_mode import ActionMode
from squid_ui.content.emoji import Emoji
from squid_ui.content.entity import ConversationType, EntityRef, EntityType
from squid_ui.content.text import Markup
from squid_ui.scene.model import Body, JsonValue, SceneNode
from squid_ui.scene.values import SceneValue
from squid_ui.styles import ActionStyle, Color


@dataclass(frozen=True, slots=True)
class Text(SceneNode):
    KIND: ClassVar[str] = "text"

    content: str
    markup: Markup = Markup.PLAIN


class Time(SceneValue):
    """An ISO-8601 instant and a target timestamp style, with an optional text prefix."""

    KIND: ClassVar[str] = "time"

    instant: str = Field(json_schema_extra={"format": "date-time"})
    style: str = Field(pattern=r"^(?:t|T|d|D|f|F|R)$")
    prefix: str | None = None


class ZonedTime(SceneValue):
    KIND: ClassVar[str] = "zoned_time"

    instant: str = Field(json_schema_extra={"format": "date-time"})
    timezone: str = Field(min_length=1)
    prefix: str | None = None


class File(SceneValue):
    """A visible file reference; bytes are supplied separately under `asset_key`."""

    KIND: ClassVar[str] = "file"

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = Field(default=False, strict=True)


class Separator(SceneValue):
    KIND: ClassVar[str] = "separator"

    large: bool = Field(default=False, strict=True)
    visible: bool = Field(default=True, strict=True)


@dataclass(frozen=True, slots=True)
class Link(SceneNode):
    KIND: ClassVar[str] = "link"

    label: str | None
    url: Annotated[str, Field(max_length=512)]
    emoji: Emoji | None = None
    disabled: Annotated[bool, Field(strict=True)] = False


class PremiumButton(SceneValue):
    KIND: ClassVar[str] = "premium_button"

    sku_id: int = Field(ge=1, strict=True)


@dataclass(frozen=True, slots=True)
class Button(SceneNode):
    """A resolved button whose action key identifies a process-local binding."""

    KIND: ClassVar[str] = "button"

    label: str | None
    action: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedButton(SceneNode):
    """A button carrying a durable route ID instead of a process-local handler.

    Hosts dispatch the route without a live UI session; the codec preserves the
    identifier but does not check whether a host has registered it.
    """

    KIND: ClassVar[str] = "routed_button"

    label: str | None
    route_id: Annotated[str, Field(max_length=100)]
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class Option(SceneNode):
    label: str
    value: str
    description: str | None = None
    default: Annotated[bool, Field(strict=True)] = False
    emoji: Emoji | None = None


@dataclass(frozen=True, slots=True)
class Select(SceneNode):
    KIND: ClassVar[str] = "select"

    options: tuple[Option, ...]
    action: str
    placeholder: str | None = None
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedSelect(SceneNode):
    KIND: ClassVar[str] = "routed_select"

    options: Annotated[tuple[Option, ...], Field(max_length=25)]
    route_id: Annotated[str, Field(max_length=100)]
    placeholder: str | None = None
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class EntitySelect(SceneNode):
    KIND: ClassVar[str] = "entity_select"

    entity_type: EntityType
    action: str
    placeholder: Annotated[str, Field(max_length=150)] | None = None
    default_values: Annotated[tuple[EntityRef, ...], Field(max_length=25)] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    min_values: Annotated[int, Field(ge=0, le=25, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, le=25, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class Row(SceneNode):
    KIND: ClassVar[str] = "row"

    items: tuple[Link | PremiumButton | Button | RoutedButton | Extension, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail(SceneNode):
    KIND: ClassVar[str] = "thumbnail"

    url: str
    description: str | None = None
    spoiler: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class GalleryItem(SceneNode):
    url: str
    description: str | None = None
    spoiler: Annotated[bool, Field(strict=True)] = False
    asset_name: str | None = None


@dataclass(frozen=True, slots=True)
class Gallery(SceneNode):
    KIND: ClassVar[str] = "gallery"

    items: tuple[GalleryItem, ...]


@dataclass(frozen=True, slots=True)
class Section(SceneNode):
    KIND: ClassVar[str] = "section"

    texts: tuple[Text, ...]
    accessory: Thumbnail | Link | PremiumButton | Button | RoutedButton | Extension


@dataclass(frozen=True, slots=True)
class Panel(SceneNode):
    KIND: ClassVar[str] = "panel"

    children: tuple[Node, ...]
    accent: Annotated[Color, Field(ge=0, le=0xFFFFFF, strict=True)] | None = None
    spoiler: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class Extension(SceneNode):
    """Versioned target payload prepared by a registered extension adapter."""

    KIND: ClassVar[str] = "extension"

    kind: str
    version: Annotated[int, Field(ge=0, strict=True)]
    payload: Mapping[str, JsonValue]


type Node = (
    Text
    | Time
    | ZonedTime
    | File
    | Separator
    | Link
    | Button
    | Row
    | Select
    | RoutedSelect
    | EntitySelect
    | RoutedButton
    | PremiumButton
    | Thumbnail
    | Gallery
    | Section
    | Panel
    | Extension
)


@dataclass(frozen=True, slots=True)
class ComponentsV2(Body):
    """A Components V2 message: the component tree is the whole message."""

    KIND: ClassVar[str] = "components_v2"

    children: tuple[Node, ...] = ()


class EmbedField(SceneValue):
    name: str
    value: str
    inline: bool = Field(default=False, strict=True)


class EmbedAuthor(SceneValue):
    name: str
    url: str | None = None
    icon_url: str | None = None


class EmbedFooter(SceneValue):
    text: str
    icon_url: str | None = None


class EmbedMedia(SceneValue):
    """One embed image or thumbnail. The description is kept even where Discord drops it."""

    url: str
    description: str | None = None
    asset_name: str | None = None


class Embed(SceneValue):
    """One resolved embed after grouping and overflow decisions.

    Only authored properties are represented. Provider details, video metadata,
    and other values Discord derives when unfurling a URL are outside the scene.
    """

    title: str | None = None
    url: str | None = None
    description: str | None = None
    # The planner must be able to inspect an overfull candidate and report a
    # layout violation, so Discord's field count limit is checked there.
    fields: tuple[EmbedField, ...] = ()
    footer: EmbedFooter | None = None
    author: EmbedAuthor | None = None
    colour: Color | None = Field(default=None, strict=True, ge=0, le=0xFFFFFF)
    image: EmbedMedia | None = None
    thumbnail: EmbedMedia | None = None
    timestamp: str | None = None
    """An ISO-8601 instant, or None. Stored as text so the scene stays plain data."""


type Control = Link | PremiumButton | Button | RoutedButton | Select | RoutedSelect | EntitySelect | Extension


@dataclass(frozen=True, slots=True)
class ClassicRow(SceneNode):
    """One classic action row. Row assignment is a planning decision, not a drawing one."""

    controls: Annotated[tuple[Control, ...], Field(max_length=5)] = ()


@dataclass(frozen=True, slots=True)
class ClassicMessage(Body):
    """A pre-Components-V2 message: content, embeds, and up to five action rows."""

    KIND: ClassVar[str] = "classic_message"

    content: str | None = None
    embeds: tuple[Embed, ...] = ()
    rows: Annotated[tuple[ClassicRow, ...], Field(max_length=5)] = ()


ComponentsV2.DISPLAY_FIELDS = ("children",)
ClassicMessage.DISPLAY_FIELDS = ("embeds", "rows")
ClassicRow.DISPLAY_FIELDS = ("controls",)
Embed.DISPLAY_FIELDS = ("author", "fields", "thumbnail", "image", "footer")
EmbedAuthor.DISPLAY_FIELDS = ()
EmbedField.DISPLAY_FIELDS = ()
EmbedFooter.DISPLAY_FIELDS = ()
EmbedMedia.DISPLAY_FIELDS = ()
Text.DISPLAY_FIELDS = ()
Time.DISPLAY_FIELDS = ()
ZonedTime.DISPLAY_FIELDS = ()
File.DISPLAY_FIELDS = ()
Separator.DISPLAY_FIELDS = ()
Link.DISPLAY_FIELDS = ()
PremiumButton.DISPLAY_FIELDS = ()
Button.DISPLAY_FIELDS = ()
RoutedButton.DISPLAY_FIELDS = ()
Option.DISPLAY_FIELDS = ()
Select.DISPLAY_FIELDS = ("options",)
RoutedSelect.DISPLAY_FIELDS = ("options",)
EntitySelect.DISPLAY_FIELDS = ()
Row.DISPLAY_FIELDS = ("items",)
Thumbnail.DISPLAY_FIELDS = ()
GalleryItem.DISPLAY_FIELDS = ()
Gallery.DISPLAY_FIELDS = ("items",)
Section.DISPLAY_FIELDS = ("texts", "accessory")
Panel.DISPLAY_FIELDS = ("children",)
Extension.DISPLAY_FIELDS = ()

RoutedButton.ROUTE_FIELDS = ("route_id",)
RoutedSelect.ROUTE_FIELDS = ("route_id",)

"""The output of layout planning, ready for a target renderer.

An authored layout may allow several representations, such as a group of buttons
or a select menu. A Scene records the representation the planner chose, with text
already resolved and controls placed in the target's structure. The renderer
draws that choice rather than making another layout decision.

Scene nodes contain display values and identifiers that can be serialized. The
accompanying PlanResult keeps Python callbacks and delivery resources under those
identifiers, so a frontend can connect a displayed control to its handler without
putting the handler itself in the scene. Most limits are checked during planning
or schema validation, rather than by these dataclass constructors.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from squid_ui._dataclass import immutable
from squid_ui._query_index import QueryIndexOwner
from squid_ui.action_mode import ActionMode
from squid_ui.content.emoji import Emoji
from squid_ui.content.entity import ConversationType, EntityRef, EntityType
from squid_ui.content.text import Markup
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.scene.slack import SlackHomeView, SlackMessage, SlackModalView


class _SceneValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("instant", check_fields=False)
    @classmethod
    def _aware_instant(cls, value: str) -> str:
        # Keep the instant's source text for renderers while checking the wire format.
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            message = "instant must be an ISO-8601 date-time"
            raise ValueError(message) from error
        if parsed.utcoffset() is None:
            message = "instant must include a UTC offset"
            raise ValueError(message)
        return value


@dataclass(frozen=True, slots=True)
class Text:
    KIND: ClassVar[str] = "text"

    content: str
    markup: Markup = Markup.PLAIN


class Time(_SceneValue):
    """An ISO-8601 instant and a target timestamp style, with an optional text prefix."""

    KIND: ClassVar[str] = "time"

    instant: str = Field(json_schema_extra={"format": "date-time"})
    style: str = Field(pattern=r"^(?:t|T|d|D|f|F|R)$")
    prefix: str | None = None


class ZonedTime(_SceneValue):
    KIND: ClassVar[str] = "zoned_time"

    instant: str = Field(json_schema_extra={"format": "date-time"})
    timezone: str = Field(min_length=1)
    prefix: str | None = None


class File(_SceneValue):
    """A visible file reference; bytes are supplied separately under `asset_key`."""

    KIND: ClassVar[str] = "file"

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = Field(default=False, strict=True)


class Separator(_SceneValue):
    KIND: ClassVar[str] = "separator"

    large: bool = Field(default=False, strict=True)
    visible: bool = Field(default=True, strict=True)


@dataclass(frozen=True, slots=True)
class Link:
    KIND: ClassVar[str] = "link"

    label: str | None
    url: Annotated[str, Field(max_length=512)]
    emoji: Emoji | None = None
    disabled: Annotated[bool, Field(strict=True)] = False


class PremiumButton(_SceneValue):
    KIND: ClassVar[str] = "premium_button"

    sku_id: int = Field(ge=1, strict=True)


@dataclass(frozen=True, slots=True)
class Button:
    """A resolved button whose action key identifies a process-local binding."""

    KIND: ClassVar[str] = "button"

    label: str | None
    action: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedButton:
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
class Option:
    label: str
    value: str
    description: str | None = None
    default: Annotated[bool, Field(strict=True)] = False
    emoji: Emoji | None = None


@dataclass(frozen=True, slots=True)
class Select:
    KIND: ClassVar[str] = "select"

    options: tuple[Option, ...]
    action: str
    placeholder: str | None = None
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedSelect:
    KIND: ClassVar[str] = "routed_select"

    options: Annotated[tuple[Option, ...], Field(max_length=25)]
    route_id: Annotated[str, Field(max_length=100)]
    placeholder: str | None = None
    min_values: Annotated[int, Field(ge=0, strict=True)] = 1
    max_values: Annotated[int, Field(ge=0, strict=True)] = 1
    disabled: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class EntitySelect:
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
class Row:
    KIND: ClassVar[str] = "row"

    items: tuple[Link | PremiumButton | Button | RoutedButton | Extension, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail:
    KIND: ClassVar[str] = "thumbnail"

    url: str
    description: str | None = None
    spoiler: Annotated[bool, Field(strict=True)] = False


@dataclass(frozen=True, slots=True)
class GalleryItem:
    url: str
    description: str | None = None
    spoiler: Annotated[bool, Field(strict=True)] = False
    asset_name: str | None = None


@dataclass(frozen=True, slots=True)
class Gallery:
    KIND: ClassVar[str] = "gallery"

    items: tuple[GalleryItem, ...]


@dataclass(frozen=True, slots=True)
class Section:
    KIND: ClassVar[str] = "section"

    texts: tuple[Text, ...]
    accessory: Thumbnail | Link | PremiumButton | Button | RoutedButton | Extension


@dataclass(frozen=True, slots=True)
class Panel:
    KIND: ClassVar[str] = "panel"

    children: tuple[Node, ...]
    accent: Annotated[Color, Field(ge=0, le=0xFFFFFF, strict=True)] | None = None
    spoiler: Annotated[bool, Field(strict=True)] = False


type JsonValue = str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None
"""Recursive JSON-compatible values used in extension payload annotations."""


@dataclass(frozen=True, slots=True)
class Extension:
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


# --- Semantic HTML --------------------------------------------------------------------------


class HtmlTag(StrEnum):
    """The complete element allowlist accepted by the semantic HTML scene."""

    MAIN = "main"
    SECTION = "section"
    ARTICLE = "article"
    ASIDE = "aside"
    HEADER = "header"
    FOOTER = "footer"
    NAV = "nav"
    H1 = "h1"
    H2 = "h2"
    H3 = "h3"
    H4 = "h4"
    H5 = "h5"
    H6 = "h6"
    P = "p"
    SMALL = "small"
    STRONG = "strong"
    EM = "em"
    S = "s"
    SPAN = "span"
    DIV = "div"
    UL = "ul"
    OL = "ol"
    LI = "li"
    DL = "dl"
    DT = "dt"
    DD = "dd"
    TABLE = "table"
    CAPTION = "caption"
    THEAD = "thead"
    TBODY = "tbody"
    TR = "tr"
    TH = "th"
    TD = "td"
    BLOCKQUOTE = "blockquote"
    PRE = "pre"
    CODE = "code"
    FIGURE = "figure"
    FIGCAPTION = "figcaption"
    DETAILS = "details"
    SUMMARY = "summary"
    PROGRESS = "progress"
    A = "a"
    IMG = "img"
    FORM = "form"
    FIELDSET = "fieldset"
    LEGEND = "legend"
    LABEL = "label"
    INPUT = "input"
    TEXTAREA = "textarea"
    SELECT = "select"
    OPTION = "option"
    BUTTON = "button"
    TIME = "time"
    BR = "br"
    HR = "hr"


class HtmlAttributeName(StrEnum):
    """Safe ordinary attributes; URLs, actions, colours, and assets use typed references."""

    CLASS = "class"
    ID = "id"
    ROLE = "role"
    ARIA_LABEL = "aria-label"
    ARIA_DESCRIBEDBY = "aria-describedby"
    ARIA_CURRENT = "aria-current"
    ARIA_DISABLED = "aria-disabled"
    ALT = "alt"
    TITLE = "title"
    FOR = "for"
    TYPE = "type"
    NAME = "name"
    VALUE = "value"
    PLACEHOLDER = "placeholder"
    MIN = "min"
    MAX = "max"
    MINLENGTH = "minlength"
    MAXLENGTH = "maxlength"
    STEP = "step"
    SCOPE = "scope"
    LANG = "lang"
    OPEN = "open"
    DISABLED = "disabled"
    REQUIRED = "required"
    MULTIPLE = "multiple"
    SELECTED = "selected"
    CHECKED = "checked"
    DOWNLOAD = "download"
    REL = "rel"
    TONE = "tone"
    EMPHASIS = "emphasis"
    DISPLAY = "display"
    SELECTION_MIN = "selection-min"
    SELECTION_MAX = "selection-max"
    TIMEZONE = "timezone"
    TIME_STYLE = "time-style"
    ENTITY_TYPE = "entity-type"
    CONVERSATION_TYPES = "conversation-types"


type HtmlAttributeValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class HtmlAttribute:
    """One allowlisted ordinary HTML attribute with an escapable scalar value."""

    name: HtmlAttributeName
    value: HtmlAttributeValue

    def __post_init__(self) -> None:
        if not isinstance(self.name, HtmlAttributeName):
            message = f"unsupported HTML scene attribute {self.name!r}"
            raise TypeError(message)
        if isinstance(self.value, float) and not math.isfinite(self.value):
            message = "HTML attribute value must be finite"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class HtmlActionRef:
    """A host-dispatched action reference; no transport behavior crosses the scene."""

    action: str
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class HtmlRouteRef:
    """A durable route identifier dispatched by the browser host."""

    route_id: str


@dataclass(frozen=True, slots=True)
class HtmlFormRef:
    """A stable form binding and optional submitted field name."""

    key: str
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class HtmlUrlRef:
    """A URL whose scheme the HTML renderer validates before emitting it."""

    url: str


@dataclass(frozen=True, slots=True)
class HtmlTimeRef:
    """An ISO-8601 instant with optional portable display metadata."""

    instant: str
    timezone: str | None = None
    style: str | None = None


@dataclass(frozen=True, slots=True)
class HtmlColourRef:
    """An sRGB colour validated as an integer from 0x000000 through 0xFFFFFF."""

    value: Color

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            message = "HTML colour must be an integer"
            raise TypeError(message)
        if not 0 <= self.value <= 0xFFFFFF:
            message = "HTML colour must be an integer from 0x000000 to 0xffffff"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class HtmlAssetRef:
    """A declared scene asset referenced without embedding its source or transport URL."""

    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class HtmlText:
    """Escapable text, optionally carrying safe Markdown for token conversion."""

    KIND: ClassVar[str] = "html_text"

    content: str
    markup: Markup = Markup.PLAIN


@dataclass(frozen=True, slots=True)
class HtmlElement:
    """One allowlisted element with typed references and no raw HTML or style escape hatch."""

    KIND: ClassVar[str] = "html_element"

    tag: HtmlTag
    children: tuple[HtmlNode, ...] = ()
    attributes: tuple[HtmlAttribute, ...] = ()
    action: HtmlActionRef | None = None
    route: HtmlRouteRef | None = None
    form: HtmlFormRef | None = None
    url: HtmlUrlRef | None = None
    time: HtmlTimeRef | None = None
    colour: HtmlColourRef | None = None
    asset: HtmlAssetRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tag, HtmlTag):
            message = f"unsupported HTML scene tag {self.tag!r}"
            raise TypeError(message)
        if any(not isinstance(attribute.name, HtmlAttributeName) for attribute in self.attributes):
            message = "unsupported HTML scene attribute"
            raise TypeError(message)
        names = [attribute.name for attribute in self.attributes]
        if len(names) != len(set(names)):
            message = "HTML scene attributes must be unique on an element"
            raise ValueError(message)


type HtmlNode = HtmlText | HtmlElement


@dataclass(frozen=True, slots=True)
class HtmlBody:
    """A semantic HTML fragment whose text and typed references are interpreted by a renderer."""

    KIND: ClassVar[str] = "html"

    children: tuple[HtmlNode, ...] = ()
    locale: str | None = None


# --- Message bodies -------------------------------------------------------------------------
#
# Discord targets have two distinct body shapes: a Components V2 tree, or classic
# content, embeds, and action rows. Separate types keep consumers from interpreting
# a classic message as a V2 tree. HTML and Slack targets have their own body types.


@dataclass(frozen=True, slots=True)
class ComponentsV2:
    """A Components V2 message: the component tree is the whole message."""

    KIND: ClassVar[str] = "components_v2"

    children: tuple[Node, ...] = ()


class EmbedField(_SceneValue):
    name: str
    value: str
    inline: bool = Field(default=False, strict=True)


class EmbedAuthor(_SceneValue):
    name: str
    url: str | None = None
    icon_url: str | None = None


class EmbedFooter(_SceneValue):
    text: str
    icon_url: str | None = None


class EmbedMedia(_SceneValue):
    """One embed image or thumbnail. The description is kept even where Discord drops it."""

    url: str
    description: str | None = None
    asset_name: str | None = None


class Embed(_SceneValue):
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
class ClassicRow:
    """One classic action row. Row assignment is a planning decision, not a drawing one."""

    controls: Annotated[tuple[Control, ...], Field(max_length=5)] = ()


@dataclass(frozen=True, slots=True)
class ClassicMessage:
    """A pre-Components-V2 message: content, embeds, and up to five action rows."""

    KIND: ClassVar[str] = "classic_message"

    content: str | None = None
    embeds: tuple[Embed, ...] = ()
    rows: Annotated[tuple[ClassicRow, ...], Field(max_length=5)] = ()


type Body = ComponentsV2 | ClassicMessage | HtmlBody | SlackMessage | SlackModalView | SlackHomeView


_KIND_OWNERS: dict[str, type] = {}
for _kind_cls in (
    Text,
    Time,
    ZonedTime,
    File,
    Separator,
    Link,
    PremiumButton,
    Button,
    RoutedButton,
    Select,
    RoutedSelect,
    EntitySelect,
    Row,
    Thumbnail,
    Gallery,
    Section,
    Panel,
    Extension,
    HtmlText,
    HtmlElement,
    ComponentsV2,
    ClassicMessage,
    HtmlBody,
    SlackMessage,
    SlackModalView,
    SlackHomeView,
):
    if _kind_cls.KIND in _KIND_OWNERS:
        # A reused tag would let the codec's `match kind:` misroute an unrelated node type.
        message = f"scene kind tag {_kind_cls.KIND!r} is used by both {_KIND_OWNERS[_kind_cls.KIND].__name__} and {_kind_cls.__name__}"
        raise AssertionError(message)
    _KIND_OWNERS[_kind_cls.KIND] = _kind_cls
del _kind_cls


class Asset(_SceneValue):
    """An asset declaration keyed to a separately supplied delivery resource."""

    key: str
    name: str
    media_type: str


class Pager(_SceneValue):
    """A paginator's zero-based selected page, total page count, and content identity."""

    key: str
    page: int = Field(ge=0, strict=True)
    pages: int = Field(ge=1, strict=True)
    content_fingerprint: str

    def browser_state(self) -> dict[str, Any]:
        """Return the page coordinates exposed to preview and HTML hosts."""
        # Content identity stays server-side; browser controls only need coordinates.
        return self.model_dump(mode="json", include={"key", "page", "pages"})


@immutable
class Scene[BodyT = Body](QueryIndexOwner):
    """One planned display, including its target, content, assets, and selected pages.

    The body holds the target's content structure: for example, classic Discord
    messages have content, embeds, and action rows. Callbacks and native frontend
    objects belong to PlanResult and do not cross the scene codec.

    Frozen dataclasses prevent attribute reassignment but do not freeze mappings
    nested in extension payloads. Preserve those values after planning so a scene's
    fingerprint continues to identify the same content.
    """

    protocol: int
    target: str
    target_version: int
    body: BodyT
    assets: tuple[Asset, ...] = ()
    pagers: tuple[Pager, ...] = ()

    @property
    def components_v2(self) -> ComponentsV2:
        """The Components V2 body, for a caller that only speaks V2.

        Raises:
            LayoutInvariantError: This scene resolved to some other kind of message.
        """
        if not isinstance(self.body, ComponentsV2):
            message = f"scene for target {self.target!r} has a {type(self.body).__name__} body, not Components V2"
            raise LayoutInvariantError(message)
        return self.body

    def expect_body[ExpectedT](self, body_type: type[ExpectedT]) -> ExpectedT:
        """Return the body as the requested type, or raise `LayoutInvariantError`."""
        if not isinstance(self.body, body_type):
            message = (
                f"scene for target {self.target!r} has a {type(self.body).__name__} body, not {body_type.__name__}"
            )
            raise LayoutInvariantError(message)
        return self.body

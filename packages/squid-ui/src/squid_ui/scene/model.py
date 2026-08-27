"""Immutable, serializable output of target planning."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from squid_ui.emoji import Emoji
from squid_ui.entity import ChannelType, EntityRef, EntityType
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.runtime.presentation_state import SessionUpdate
from squid_ui.text import Markup


@dataclass(frozen=True, slots=True)
class Text:
    KIND: ClassVar[str] = "text"

    content: str
    markup: Markup = Markup.DISCORD_MARKDOWN


@dataclass(frozen=True, slots=True)
class Time:
    KIND: ClassVar[str] = "time"

    instant: str
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class ZonedTime:
    KIND: ClassVar[str] = "zoned_time"

    instant: str
    timezone: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class File:
    KIND: ClassVar[str] = "file"

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Separator:
    KIND: ClassVar[str] = "separator"

    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class Link:
    KIND: ClassVar[str] = "link"

    label: str | None
    url: str
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class PremiumButton:
    KIND: ClassVar[str] = "premium_button"

    sku_id: int


@dataclass(frozen=True, slots=True)
class Button:
    KIND: ClassVar[str] = "button"

    label: str | None
    action: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedButton:
    """A button carrying its own route id, with no binding for a frontend to wire.

    That absence is the point: a renderer can draw one without a live session, which is
    what lets a sessionless document hold a control, and a codec can round-trip one,
    which a process-local handler could never be.
    """

    KIND: ClassVar[str] = "routed_button"

    label: str | None
    route_id: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class Option:
    label: str
    value: str
    description: str | None = None
    default: bool = False
    emoji: Emoji | None = None


@dataclass(frozen=True, slots=True)
class Select:
    KIND: ClassVar[str] = "select"

    options: tuple[Option, ...]
    action: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedSelect:
    KIND: ClassVar[str] = "routed_select"

    options: tuple[Option, ...]
    route_id: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class EntitySelect:
    KIND: ClassVar[str] = "entity_select"

    entity_type: EntityType
    action: str
    placeholder: str | None = None
    default_values: tuple[EntityRef, ...] = ()
    channel_types: tuple[ChannelType, ...] = ()
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
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
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class GalleryItem:
    url: str
    description: str | None = None
    spoiler: bool = False


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
    accent: Color | None = None
    spoiler: bool = False


type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
"""What may cross the scene codec. Stated by the type rather than only by prose."""


@dataclass(frozen=True, slots=True)
class Extension:
    """Versioned target payload prepared by a registered extension adapter."""

    KIND: ClassVar[str] = "extension"

    kind: str
    version: int
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
    CHANNEL_TYPES = "channel-types"


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
    """An exact sRGB colour, constrained to six hexadecimal digits by its integer type."""

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
    """A semantic HTML fragment whose nodes are safe plain data."""

    KIND: ClassVar[str] = "html"

    children: tuple[HtmlNode, ...] = ()
    locale: str | None = None


# --- Message bodies -------------------------------------------------------------------------
#
# A scene resolves to *one* Discord message, and Discord has two kinds. A Components V2
# message is a component tree and has no content or embeds at all; a classic message is
# content, embeds, and action rows and cannot hold a component tree. Modelling both as one
# flat child list would force every consumer to rediscover which kind it was holding, so the
# body says so once.


@dataclass(frozen=True, slots=True)
class ComponentsV2:
    """A Components V2 message: the component tree is the whole message."""

    KIND: ClassVar[str] = "components_v2"

    children: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class EmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True, slots=True)
class EmbedAuthor:
    name: str
    url: str | None = None
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class EmbedFooter:
    text: str
    icon_url: str | None = None


@dataclass(frozen=True, slots=True)
class EmbedMedia:
    """One embed image or thumbnail. The description is kept even where Discord drops it."""

    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Embed:
    """One resolved embed. Every grouping and overflow decision is already made.

    Server-generated properties — provider, video, and the fields Discord fills from a URL
    it unfurls — are deliberately absent. A scene describes what Squid asked for, and
    round-tripping something the API invents would make the fingerprint lie.
    """

    title: str | None = None
    url: str | None = None
    description: str | None = None
    fields: tuple[EmbedField, ...] = ()
    footer: EmbedFooter | None = None
    author: EmbedAuthor | None = None
    colour: Color | None = None
    image: EmbedMedia | None = None
    thumbnail: EmbedMedia | None = None
    timestamp: str | None = None
    """An ISO-8601 instant, or None. Stored as text so the scene stays plain data."""


type Control = Link | PremiumButton | Button | RoutedButton | Select | RoutedSelect | EntitySelect | Extension


@dataclass(frozen=True, slots=True)
class ClassicRow:
    """One classic action row. Row assignment is a planning decision, not a drawing one."""

    controls: tuple[Control, ...] = ()


@dataclass(frozen=True, slots=True)
class ClassicMessage:
    """A pre-Components-V2 message: content, embeds, and up to five action rows."""

    KIND: ClassVar[str] = "classic_message"

    content: str | None = None
    embeds: tuple[Embed, ...] = ()
    rows: tuple[ClassicRow, ...] = ()


type Body = ComponentsV2 | ClassicMessage | HtmlBody


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
):
    if _kind_cls.KIND in _KIND_OWNERS:
        # A reused tag would let the codec's `match kind:` misroute an unrelated node type.
        message = f"scene kind tag {_kind_cls.KIND!r} is used by both {_KIND_OWNERS[_kind_cls.KIND].__name__} and {_kind_cls.__name__}"
        raise AssertionError(message)
    _KIND_OWNERS[_kind_cls.KIND] = _kind_cls
del _kind_cls


@dataclass(frozen=True, slots=True)
class Asset:
    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class Pager:
    key: str
    page: int
    pages: int
    content_fingerprint: str


@dataclass(frozen=True, slots=True)
class Scene[BodyT = Body]:
    """A target-resolved scene with no callbacks or native frontend objects."""

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
        """Narrow a broadly decoded scene at an explicit frontend boundary."""
        if not isinstance(self.body, body_type):
            message = (
                f"scene for target {self.target!r} has a {type(self.body).__name__} body, not {body_type.__name__}"
            )
            raise LayoutInvariantError(message)
        return self.body


class PlanSeverity(StrEnum):
    ADAPTATION = "adaptation"
    DEGRADATION = "degradation"
    WARNING = "warning"


class PlanReuse(StrEnum):
    """How much prior planner work produced this result."""

    MISS = "miss"
    EXACT = "exact"
    STRUCTURAL = "structural"
    INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class PlanEvent:
    code: str
    path: str
    message: str
    severity: PlanSeverity = PlanSeverity.ADAPTATION
    before: Mapping[str, int] = field(default_factory=dict)
    after: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanReport:
    events: tuple[PlanEvent, ...] = ()
    logical_fingerprint: str = ""
    scene_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class PlanMetrics:
    """Planner instrumentation kept outside deterministic reports and scene payloads."""

    states_explored: int = 0
    """`measure()` calls the search spent, across strategies, fallbacks, and ladder rungs."""
    cache_hit: bool = False
    reuse: PlanReuse = PlanReuse.MISS
    search_fallback: bool = False


@dataclass(frozen=True, slots=True)
class PlanResult[BodyT = Body]:
    scene: Scene[BodyT]
    bindings: Mapping[str, ActionBinding]
    report: PlanReport
    form_bindings: Mapping[str, FormBinding] = field(default_factory=dict)
    """What each declared form key presents right now, for resolving a late submission."""
    resources: Mapping[str, object] = field(default_factory=dict)
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
    session_updates: tuple[SessionUpdate, ...] = ()
    """Presentation writes this plan earned but did not make.

    Planning only reads the session. A frontend applies these once the render has
    actually reached the reader, so a failed delivery leaves them where the message
    still shows them."""

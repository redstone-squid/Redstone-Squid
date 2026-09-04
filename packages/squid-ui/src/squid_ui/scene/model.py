"""Immutable, serializable output of target planning."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from squid_ui.emoji import Emoji
from squid_ui.entity import ConversationType, EntityRef, EntityType
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.runtime.presentation_state import SessionUpdate
from squid_ui.scene.slack import SlackHomeView, SlackMessage, SlackModalView
from squid_ui.text import Markup


@dataclass(frozen=True, slots=True)
class Text:
    """A `TextDisplay` component.

    `content` is already escaped for `markup` and counts against the message's shared
    4000-character text budget.
    """

    KIND: ClassVar[str] = "text"

    content: str
    markup: Markup = Markup.DISCORD_MARKDOWN


@dataclass(frozen=True, slots=True)
class Time:
    """Drawn as a Discord `<t:unix:style>` tag; `style` is one of its letters `t T d D f F R`."""

    KIND: ClassVar[str] = "time"

    instant: str
    """An ISO-8601 instant, stored as text so the scene stays plain data."""
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class ZonedTime:
    """Drawn as literal ISO-8601 text in `timezone`, for an instant that must not shift with the reader."""

    KIND: ClassVar[str] = "zoned_time"

    instant: str
    timezone: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class File:
    """A Components V2 file component; `asset_key` names the `Scene.assets` entry that carries its bytes."""

    KIND: ClassVar[str] = "file"

    asset_key: str
    name: str
    media_type: str
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Separator:
    """A Components V2 separator; `visible=False` keeps the spacing but drops the line."""

    KIND: ClassVar[str] = "separator"

    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class Link:
    """A link-style button; Discord opens `url` itself, so it carries no action and needs no binding."""

    KIND: ClassVar[str] = "link"

    label: str | None
    url: str
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class PremiumButton:
    """Discord's premium-upsell button for `sku_id`; Discord fixes its label and style."""

    KIND: ClassVar[str] = "premium_button"

    sku_id: int


@dataclass(frozen=True, slots=True)
class Button:
    """A session-bound button; `action` is a `PlanResult.bindings` key, so drawing it needs the live plan."""

    KIND: ClassVar[str] = "button"

    label: str | None
    action: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedButton:
    """A button dispatched by `route_id` through the host's route registry, with no session binding.

    A renderer draws it without a live plan and the codec round-trips it, which is what lets
    a stored or sessionless document keep a working control.
    """

    KIND: ClassVar[str] = "routed_button"

    label: str | None
    route_id: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: Emoji | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class Option:
    """One `SelectOption`; `label` and `value` are at most 100 characters, and `value` is what the interaction reports."""

    label: str
    value: str
    description: str | None = None
    default: bool = False
    emoji: Emoji | None = None


@dataclass(frozen=True, slots=True)
class Select:
    """A string select bound to `action`, drawn in an `ActionRow` of its own; at most 25 `options`."""

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
    """A `Select` dispatched by `route_id` through the host's route registry, so it needs no session binding."""

    KIND: ClassVar[str] = "routed_select"

    options: tuple[Option, ...]
    route_id: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class EntitySelect:
    """A native user, role, or conversation picker; `entity_type` chooses the Discord select component."""

    KIND: ClassVar[str] = "entity_select"

    entity_type: EntityType
    action: str
    placeholder: str | None = None
    default_values: tuple[EntityRef, ...] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    """Filter for `EntityType.CONVERSATION` only; empty means every type the host offers."""
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class Row:
    """An `ActionRow` of at most five buttons; selects never appear here, each takes a row of its own."""

    KIND: ClassVar[str] = "row"

    items: tuple[Link | PremiumButton | Button | RoutedButton | Extension, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail:
    """A `Thumbnail` component; only legal as a `Section.accessory`."""

    KIND: ClassVar[str] = "thumbnail"

    url: str
    description: str | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class GalleryItem:
    """One `MediaGalleryItem`; `description` is its alt text, at most 1024 characters."""

    url: str
    description: str | None = None
    spoiler: bool = False


@dataclass(frozen=True, slots=True)
class Gallery:
    """A `MediaGallery` of 1-10 items."""

    KIND: ClassVar[str] = "gallery"

    items: tuple[GalleryItem, ...]


@dataclass(frozen=True, slots=True)
class Section:
    """A `Section` component: 1-3 `texts` beside one `accessory`."""

    KIND: ClassVar[str] = "section"

    texts: tuple[Text, ...]
    accessory: Thumbnail | Link | PremiumButton | Button | RoutedButton | Extension


@dataclass(frozen=True, slots=True)
class Panel:
    """A `Container`: a bordered group with an optional `accent` stripe; `spoiler` blurs the whole group."""

    KIND: ClassVar[str] = "panel"

    children: tuple[Node, ...]
    accent: Color | None = None
    spoiler: bool = False


type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
"""What an `Extension.payload` may hold: only values the scene codec can serialize."""


@dataclass(frozen=True, slots=True)
class Extension:
    """Versioned target payload prepared by a registered extension adapter.

    `Codec.to_dict` normalizes `payload` through `json` and raises `CodecError` when it is not
    serializable, so the failure lands at planning rather than in a remote renderer.
    """

    KIND: ClassVar[str] = "extension"

    kind: str
    """The extension's registered name; serialized under the JSON key `extension`, since `kind`
    is the node-type tag."""
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
    """One allowlisted ordinary HTML attribute with an escapable scalar value.

    `__post_init__` raises `TypeError` when `name` is not an `HtmlAttributeName`.
    """

    name: HtmlAttributeName
    value: HtmlAttributeValue

    def __post_init__(self) -> None:
        if not isinstance(self.name, HtmlAttributeName):
            message = f"unsupported HTML scene attribute {self.name!r}"
            raise TypeError(message)


@dataclass(frozen=True, slots=True)
class HtmlActionRef:
    """A `PlanResult.bindings` key the browser host dispatches by name; the scene carries no handler."""

    action: str
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class HtmlRouteRef:
    """A durable route identifier dispatched by the browser host."""

    route_id: str


@dataclass(frozen=True, slots=True)
class HtmlFormRef:
    """Ties an element to a `PlanResult.form_bindings` key.

    `field_name` is the field an input submits under; it is `None` on the `form` element and
    its submit button.
    """

    key: str
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class HtmlUrlRef:
    """An `href` or `src`, only on `a` or `img`; the renderer drops any URL that is not absolute `http(s)`."""

    url: str


@dataclass(frozen=True, slots=True)
class HtmlTimeRef:
    """An ISO-8601 instant, only on a `time` element; drawn as `datetime` plus `data-squid-*` hints.

    `style` is a Discord timestamp letter for a reader-local display; `timezone` pins a zone.
    """

    instant: str
    timezone: str | None = None
    style: str | None = None


@dataclass(frozen=True, slots=True)
class HtmlColourRef:
    """An exact sRGB colour as an integer from `0x000000` to `0xFFFFFF`.

    `__post_init__` raises `TypeError` for a non-integer (including `bool`) and `ValueError`
    when the value is out of range.
    """

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
    """Names one `Scene.assets` entry, only on an `a` element.

    The HTML renderer resolves it to an `href` through its asset resolver, a data URL, or a
    `StoredAsset` reference, and draws the anchor `aria-disabled` when none applies.
    """

    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class HtmlText:
    """A text node; `PLAIN` is HTML-escaped whole, any other `markup` is tokenized as Markdown into inline tags."""

    KIND: ClassVar[str] = "html_text"

    content: str
    markup: Markup = Markup.PLAIN


@dataclass(frozen=True, slots=True)
class HtmlElement:
    """One allowlisted element with typed references and no raw HTML or style escape hatch.

    `__post_init__` raises `TypeError` for a `tag` or attribute name outside the allowlist and
    `ValueError` when two attributes share a name.
    """

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
    """A semantic HTML fragment; the renderer wraps `children` in a `main` element."""

    KIND: ClassVar[str] = "html"

    children: tuple[HtmlNode, ...] = ()
    locale: str | None = None
    """Drawn as the `lang` attribute; a full page falls back to `und` when unset."""


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


@dataclass(frozen=True, slots=True)
class Asset:
    """Metadata for one attachment; its bytes travel outside the scene in `PlanResult.resources["asset:<key>"]`."""

    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class Pager:
    """One pagination cursor the scene is showing: zero-based `page` of `pages`."""

    key: str
    page: int
    pages: int
    content_fingerprint: str
    """Hash of the paged content; a later plan that hashes differently treats the stored position as stale."""


@dataclass(frozen=True, slots=True)
class Scene[BodyT = Body]:
    """A target-resolved scene with no callbacks or native frontend objects."""

    protocol: int
    """The codec protocol; `Codec` accepts only 1."""
    target: str
    """The planning target id, such as `discord.components-v2`."""
    target_version: int
    """The target dialect's version; each renderer refuses a version it was not built for."""
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
        """Narrow a broadly decoded scene at an explicit frontend boundary.

        Raises `LayoutInvariantError` when the body is not a `body_type`.
        """
        if not isinstance(self.body, body_type):
            message = (
                f"scene for target {self.target!r} has a {type(self.body).__name__} body, not {body_type.__name__}"
            )
            raise LayoutInvariantError(message)
        return self.body


class PlanSeverity(StrEnum):
    """How much a `PlanEvent` cost the reader.

    `ADAPTATION` is lossless; `DEGRADATION` dropped or truncated content and fails the plan
    with `LayoutDegradedError` under `PlanRequest.strict`; `WARNING` is neither, for content
    rendered literally because it could not be interpreted.
    """

    ADAPTATION = "adaptation"
    DEGRADATION = "degradation"
    WARNING = "warning"


class PlanReuse(StrEnum):
    """How much prior planner work produced this result.

    `EXACT` replays a memoized result; `STRUCTURAL` reuses a cached scene with live values
    re-bound; `INCREMENTAL` certifies the previous layout still fits without a search.
    """

    MISS = "miss"
    EXACT = "exact"
    STRUCTURAL = "structural"
    INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class PlanEvent:
    """One planning decision, keyed by a stable dotted `code` at a `$.n.m` document `path`."""

    code: str
    path: str
    message: str
    severity: PlanSeverity = PlanSeverity.ADAPTATION
    before: Mapping[str, int] = field(default_factory=dict)
    """Measured counts per axis (`characters`, `blocks`, `options`, ...) before the decision."""
    after: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanReport:
    """The deterministic part of a plan: equal document, target, and session give an equal report."""

    events: tuple[PlanEvent, ...] = ()
    logical_fingerprint: str = ""
    """Hash of the planner's chosen layout before it is lowered to a scene; unlike `scene_fingerprint`
    it does not cover the payload."""
    scene_fingerprint: str = ""
    """`Codec.fingerprint` of the scene; equal fingerprints draw identically."""


@dataclass(frozen=True, slots=True)
class PlanMetrics:
    """Planner instrumentation kept outside deterministic reports and scene payloads."""

    states_explored: int = 0
    """`measure()` calls the search spent, across strategies, fallbacks, and ladder rungs."""
    cache_hit: bool = False
    """`True` for any `reuse` other than `MISS`."""
    reuse: PlanReuse = PlanReuse.MISS
    search_fallback: bool = False
    """The layout search ran out of budget and kept its best incumbent; a `planner.search_fallback`
    warning event says so."""


@dataclass(frozen=True, slots=True)
class PlanResult[BodyT = Body]:
    """A planned scene plus the process-local side tables a live frontend needs to serve it."""

    scene: Scene[BodyT]
    bindings: Mapping[str, ActionBinding]
    """Handlers keyed by action name; only the actions the scene actually shows are present."""
    report: PlanReport
    form_bindings: Mapping[str, FormBinding] = field(default_factory=dict)
    """What each declared form key presents right now, for resolving a late submission."""
    resources: Mapping[str, object] = field(default_factory=dict)
    """Process-local objects the scene names by key: `asset:<key>` is the `Asset` with its bytes,
    `native:<path>` a frontend object an `Extension` payload points at."""
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
    session_updates: tuple[SessionUpdate, ...] = ()
    """Presentation writes this plan earned but did not make.

    Planning only reads the session. A frontend applies these once the render has
    actually reached the reader, so a failed delivery leaves them where the message
    still shows them."""

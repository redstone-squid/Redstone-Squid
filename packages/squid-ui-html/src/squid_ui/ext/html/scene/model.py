"""Resolved Html scene vocabulary."""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import Field

from squid_ui.action_mode import ActionMode
from squid_ui.content.text import Markup
from squid_ui.scene.model import Body, SceneNode
from squid_ui.styles import Color


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
class HtmlAttribute(SceneNode):
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
class HtmlActionRef(SceneNode):
    """A host-dispatched action reference; no transport behavior crosses the scene."""

    action: str
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class HtmlRouteRef(SceneNode):
    """A durable route identifier dispatched by the browser host."""

    route_id: str


@dataclass(frozen=True, slots=True)
class HtmlFormRef(SceneNode):
    """A stable form binding and optional submitted field name."""

    key: str
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class HtmlUrlRef(SceneNode):
    """A URL whose scheme the HTML renderer validates before emitting it."""

    url: str


@dataclass(frozen=True, slots=True)
class HtmlTimeRef(SceneNode):
    """An ISO-8601 instant with optional portable display metadata."""

    instant: str
    timezone: str | None = None
    style: str | None = None


@dataclass(frozen=True, slots=True)
class HtmlColourRef(SceneNode):
    """An sRGB colour validated as an integer from 0x000000 through 0xFFFFFF."""

    value: Annotated[Color, Field(ge=0, le=0xFFFFFF, strict=True)]

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            message = "HTML colour must be an integer"
            raise TypeError(message)
        if not 0 <= self.value <= 0xFFFFFF:
            message = "HTML colour must be an integer from 0x000000 to 0xffffff"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class HtmlAssetRef(SceneNode):
    """A declared scene asset referenced without embedding its source or transport URL."""

    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class HtmlText(SceneNode):
    """Escapable text, optionally carrying safe Markdown for token conversion."""

    KIND: ClassVar[str] = "html_text"

    content: str
    markup: Markup = Markup.PLAIN


@dataclass(frozen=True, slots=True)
class HtmlElement(SceneNode):
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
class HtmlBody(Body):
    """A semantic HTML fragment whose text and typed references are interpreted by a renderer."""

    KIND: ClassVar[str] = "html"

    children: tuple[HtmlNode, ...] = ()
    locale: str | None = None


HtmlBody.DISPLAY_FIELDS = ("children",)
HtmlElement.DISPLAY_FIELDS = ("children",)
HtmlText.DISPLAY_FIELDS = ()

HtmlRouteRef.ROUTE_FIELDS = ("route_id",)

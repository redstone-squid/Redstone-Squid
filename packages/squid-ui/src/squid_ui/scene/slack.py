"""Immutable Slack Block Kit scene vocabulary."""

from dataclasses import dataclass
from enum import StrEnum

from squid_ui.entity import ConversationType
from squid_ui.interactions import ActionMode


class SlackTextKind(StrEnum):
    """Which Block Kit text object a `SlackText` draws to.

    Headers, labels, hints, placeholders, button and option text, and modal chrome accept only
    `PLAIN`; the renderer refuses `MARKDOWN` there.
    """

    PLAIN = "plain_text"
    MARKDOWN = "mrkdwn"


@dataclass(frozen=True, slots=True)
class SlackText:
    """One Block Kit text object; `content` is already escaped and truncated for its slot."""

    content: str
    kind: SlackTextKind = SlackTextKind.MARKDOWN
    emoji: bool | None = None
    """Drawn only for `PLAIN`; dropped for `MARKDOWN`."""
    verbatim: bool | None = True
    """Drawn only for `MARKDOWN`, where `True` stops Slack from auto-linking the text."""


@dataclass(frozen=True, slots=True)
class SlackOption:
    """One static-select, radio, or checkbox option.

    `label` and `description` are `plain_text` of at most 75 characters; `value` is 1-150
    characters and is what an interaction payload reports back.
    """

    label: SlackText
    value: str
    description: SlackText | None = None


class SlackButtonStyle(StrEnum):
    """Block Kit button `style`; `DEFAULT` is omitted from the drawn element rather than sent."""

    DEFAULT = "default"
    PRIMARY = "primary"
    DANGER = "danger"


@dataclass(frozen=True, slots=True)
class SlackActionRef:
    """A `PlanResult.bindings` key, drawn verbatim as the element's `action_id` (1-255 characters)."""

    action: str
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SlackRouteRef:
    """A route id drawn verbatim as the element's `action_id`; the host's route registry dispatches it.

    Needs no session binding, so a body holding only routed controls can be drawn cold.
    """

    route_id: str


@dataclass(frozen=True, slots=True)
class SlackAssetRef:
    """Names one `Scene.assets` entry by key, name, and media type.

    The renderer resolves it to an HTTPS URL through its asset resolver or a `StoredAsset` and
    refuses to draw the button without one.
    """

    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class SlackButton:
    """Lowers to a Block Kit `button` element.

    Exactly one of `action`, `route`, `url`, or `asset` is set; `__post_init__` raises
    `ValueError` otherwise. URL and asset buttons get a generated `action_id`. `label` is
    `plain_text` of 1-75 characters; `value` is at most 2000.
    """

    label: SlackText
    action: SlackActionRef | None = None
    route: SlackRouteRef | None = None
    url: str | None = None
    asset: SlackAssetRef | None = None
    value: str | None = None
    style: SlackButtonStyle = SlackButtonStyle.DEFAULT

    def __post_init__(self) -> None:
        destinations = sum(value is not None for value in (self.action, self.route, self.url, self.asset))
        if destinations != 1:
            message = "Slack buttons require exactly one action, route, URL, or asset"
            raise ValueError(message)


class SlackSelectKind(StrEnum):
    """Block Kit select family: `static_select`, `users_select`, or `conversations_select`.

    A `SlackSelect` with `maximum > 1` draws the `multi_` variant of the same family.
    """

    STATIC = "static"
    USERS = "users"
    CONVERSATIONS = "conversations"


@dataclass(frozen=True, slots=True)
class SlackSelect:
    """A static, user, or conversation selector.

    Exactly one of `action`, `route`, or `action_id` is set, and only `STATIC` carries
    `options` (at most 100) or `CONVERSATIONS` carries `conversation_types`; `__post_init__`
    raises `ValueError` otherwise. The planner emits a `STATIC` select for a form choice field
    only when it has more than ten options; fewer become checkboxes or radio buttons.
    """

    action: SlackActionRef | None = None
    route: SlackRouteRef | None = None
    action_id: str | None = None
    """The form field key, for a select inside a modal `SlackInput` rather than a live action."""
    kind: SlackSelectKind = SlackSelectKind.STATIC
    placeholder: SlackText | None = None
    options: tuple[SlackOption, ...] = ()
    initial_values: tuple[str, ...] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    minimum: int = 1
    maximum: int = 1

    def __post_init__(self) -> None:
        sources = sum(value is not None for value in (self.action, self.route, self.action_id))
        if sources != 1:
            message = "Slack selectors require exactly one action, route, or form action id"
            raise ValueError(message)
        if self.kind is not SlackSelectKind.STATIC and self.options:
            message = "native Slack selectors cannot carry static options"
            raise ValueError(message)
        if self.kind is not SlackSelectKind.CONVERSATIONS and self.conversation_types:
            message = "conversation_types is only valid for Slack conversation selectors"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class SlackTextInput:
    """Lowers to a `plain_text_input`; `multiline` is chosen for text-area form fields.

    Also the fallback for duration and date-time fields, which Block Kit has no picker for.
    """

    action_id: str
    initial_value: str | None = None
    placeholder: SlackText | None = None
    multiline: bool = False
    minimum_length: int | None = None
    maximum_length: int | None = None


@dataclass(frozen=True, slots=True)
class SlackNumberInput:
    """Lowers to a `number_input`; bounds are decimal strings because Block Kit takes them as text."""

    action_id: str
    initial_value: str | None = None
    decimal_allowed: bool = False
    minimum: str | None = None
    maximum: str | None = None


@dataclass(frozen=True, slots=True)
class SlackDatePicker:
    """Lowers to a `datepicker`; `initial_date` is `YYYY-MM-DD`."""

    action_id: str
    initial_date: str | None = None
    placeholder: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackTimePicker:
    """Lowers to a `timepicker`; `initial_time` is 24-hour `HH:mm`."""

    action_id: str
    initial_time: str | None = None
    placeholder: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackCheckboxes:
    """Lowers to a `checkboxes` element of 1-10 options.

    The planner emits it for a boolean form field (one "Yes" option) and for a multi-choice
    field with at most ten options.
    """

    action_id: str
    options: tuple[SlackOption, ...]
    initial_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackRadioButtons:
    """Lowers to a `radio_buttons` element of 1-10 options.

    The planner emits it for scale fields and single-choice fields with at most ten options.
    """

    action_id: str
    options: tuple[SlackOption, ...]
    initial_value: str | None = None


type SlackElement = (
    SlackButton
    | SlackSelect
    | SlackTextInput
    | SlackNumberInput
    | SlackDatePicker
    | SlackTimePicker
    | SlackCheckboxes
    | SlackRadioButtons
)
type SlackInputElement = (
    SlackSelect
    | SlackTextInput
    | SlackNumberInput
    | SlackDatePicker
    | SlackTimePicker
    | SlackCheckboxes
    | SlackRadioButtons
)


@dataclass(frozen=True, slots=True)
class SlackSection:
    """Lowers to a `section` block: `text` at most 3000 characters, up to 10 `fields` of 2000.

    The planner's default for prose: paragraphs, lists, quotes, code, timestamps, and bare text.
    """

    text: SlackText | None = None
    fields: tuple[SlackText, ...] = ()
    accessory: SlackElement | None = None


@dataclass(frozen=True, slots=True)
class SlackHeader:
    """Lowers to a `header` block: `plain_text` of 1-150 characters, emitted for `h1`-`h6`."""

    text: SlackText


@dataclass(frozen=True, slots=True)
class SlackContext:
    """Lowers to a `context` block of 1-10 text elements.

    The planner emits it for status text outside modals and for a disabled control, which
    becomes its label suffixed with "Unavailable".
    """

    elements: tuple[SlackText, ...]


@dataclass(frozen=True, slots=True)
class SlackDivider:
    """Lowers to a `divider` block, emitted for `hr`."""


@dataclass(frozen=True, slots=True)
class SlackImage:
    """Lowers to an `image` block; the renderer requires an absolute HTTPS `image_url`.

    `alt_text` is at most 2000 characters; `title` is `plain_text`. An image without a public
    URL is dropped at planning time with a degradation event.
    """

    image_url: str
    alt_text: str
    title: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackActions:
    """Lowers to an `actions` block of 1-25 buttons and selects.

    The planner splits a longer control group into consecutive blocks of 25.
    """

    elements: tuple[SlackButton | SlackSelect, ...]
    block_id: str | None = None


@dataclass(frozen=True, slots=True)
class SlackInput:
    """Lowers to an `input` block; only modals accept it.

    `block_id` is `<form key>:<field key>`, hashed to `form:<digest>` when that exceeds 255
    characters. `label` and `hint` are `plain_text`.
    """

    block_id: str
    label: SlackText
    element: SlackInputElement
    optional: bool = False
    hint: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackTable:
    """Lowers to a `table` block of 1-100 rows by 1-20 cells; modals reject it.

    Cell text totals at most 10000 characters; past that the planner collapses each row to
    one bullet-joined cell.
    """

    rows: tuple[tuple[SlackText, ...], ...]


@dataclass(frozen=True, slots=True)
class SlackCard:
    """Lowers to a `card` block; modals reject it.

    `title` is at most 150 characters, `description` at most 200, and `image_url` must be
    absolute HTTPS. The planner emits one for `article` and one per gallery image.
    """

    title: SlackText | None = None
    description: SlackText | None = None
    image_url: str | None = None
    actions: tuple[SlackButton, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackCarousel:
    """Lowers to a `carousel` block of 1-10 cards; modals reject it.

    The planner emits it for a multi-image gallery; a single image or a modal gallery becomes
    `SlackImage` blocks instead.
    """

    cards: tuple[SlackCard, ...]


class SlackAlertStyle(StrEnum):
    """The `level` of a Block Kit `alert`; the planner maps the `tone` attribute onto it."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SlackAlert:
    """Lowers to an `alert` block; only modals accept it.

    Drawn as `title` and `text` joined by a newline, at most 200 characters together. The
    planner emits it for status text inside a modal.
    """

    title: SlackText
    text: SlackText | None = None
    style: SlackAlertStyle = SlackAlertStyle.INFO


type SlackBlock = (
    SlackSection
    | SlackHeader
    | SlackContext
    | SlackDivider
    | SlackImage
    | SlackActions
    | SlackInput
    | SlackTable
    | SlackCard
    | SlackCarousel
    | SlackAlert
)


@dataclass(frozen=True, slots=True)
class SlackMessage:
    """A Block Kit message of at most 50 blocks; input and alert blocks are not accepted."""

    KIND = "slack_message"

    text: str
    """Notification and screen-reader fallback, at most 40000 characters; the planner fills it
    with the document's plain text."""
    blocks: tuple[SlackBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackModalView:
    """A `modal` view of at most 100 blocks; table, card, and carousel blocks are not accepted.

    `title`, `submit`, and `close` are `plain_text` of at most 24 characters; `callback_id` is
    the form key, at most 255; `private_metadata` at most 3000.
    """

    KIND = "slack_modal"

    callback_id: str
    title: SlackText
    submit: SlackText
    close: SlackText
    blocks: tuple[SlackBlock, ...] = ()
    private_metadata: str | None = None


@dataclass(frozen=True, slots=True)
class SlackHomeView:
    """A `home` view of at most 100 blocks; input and alert blocks are not accepted."""

    KIND = "slack_home"

    blocks: tuple[SlackBlock, ...] = ()
    callback_id: str | None = None
    private_metadata: str | None = None


type SlackBody = SlackMessage | SlackModalView | SlackHomeView


__all__ = [
    "SlackActionRef",
    "SlackActions",
    "SlackAlert",
    "SlackAlertStyle",
    "SlackAssetRef",
    "SlackBlock",
    "SlackBody",
    "SlackButton",
    "SlackButtonStyle",
    "SlackCard",
    "SlackCarousel",
    "SlackCheckboxes",
    "SlackContext",
    "SlackDatePicker",
    "SlackDivider",
    "SlackElement",
    "SlackHeader",
    "SlackHomeView",
    "SlackImage",
    "SlackInput",
    "SlackInputElement",
    "SlackMessage",
    "SlackModalView",
    "SlackNumberInput",
    "SlackOption",
    "SlackRadioButtons",
    "SlackRouteRef",
    "SlackSection",
    "SlackSelect",
    "SlackSelectKind",
    "SlackTable",
    "SlackText",
    "SlackTextInput",
    "SlackTextKind",
    "SlackTimePicker",
]

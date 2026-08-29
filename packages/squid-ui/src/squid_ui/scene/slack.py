"""Immutable Slack Block Kit scene vocabulary."""

from dataclasses import dataclass
from enum import StrEnum

from squid_ui.entity import ConversationType
from squid_ui.interactions import ActionMode


class SlackTextKind(StrEnum):
    """One Slack text-object representation."""

    PLAIN = "plain_text"
    MARKDOWN = "mrkdwn"


@dataclass(frozen=True, slots=True)
class SlackText:
    """Text resolved for one Slack text object."""

    content: str
    kind: SlackTextKind = SlackTextKind.MARKDOWN
    emoji: bool | None = None
    verbatim: bool | None = True


@dataclass(frozen=True, slots=True)
class SlackOption:
    """One static-select, radio, or checkbox option."""

    label: SlackText
    value: str
    description: SlackText | None = None


class SlackButtonStyle(StrEnum):
    """Styles Slack accepts on button elements."""

    DEFAULT = "default"
    PRIMARY = "primary"
    DANGER = "danger"


@dataclass(frozen=True, slots=True)
class SlackActionRef:
    """Reference to one planned action binding."""

    action: str
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SlackRouteRef:
    """Reference to one host-owned stateless route."""

    route_id: str


@dataclass(frozen=True, slots=True)
class SlackButton:
    """A Slack action or URL button."""

    label: SlackText
    action: SlackActionRef | None = None
    route: SlackRouteRef | None = None
    url: str | None = None
    value: str | None = None
    style: SlackButtonStyle = SlackButtonStyle.DEFAULT

    def __post_init__(self) -> None:
        destinations = sum(value is not None for value in (self.action, self.route, self.url))
        if destinations != 1:
            message = "Slack buttons require exactly one action, route, or URL"
            raise ValueError(message)


class SlackSelectKind(StrEnum):
    """Native selector families exposed by Block Kit."""

    STATIC = "static"
    USERS = "users"
    CONVERSATIONS = "conversations"


@dataclass(frozen=True, slots=True)
class SlackSelect:
    """A static, user, or conversation selector."""

    action: SlackActionRef | None = None
    route: SlackRouteRef | None = None
    kind: SlackSelectKind = SlackSelectKind.STATIC
    placeholder: SlackText | None = None
    options: tuple[SlackOption, ...] = ()
    initial_values: tuple[str, ...] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    minimum: int = 1
    maximum: int = 1

    def __post_init__(self) -> None:
        if (self.action is None) == (self.route is None):
            message = "Slack selectors require exactly one action or route"
            raise ValueError(message)
        if self.kind is not SlackSelectKind.STATIC and self.options:
            message = "native Slack selectors cannot carry static options"
            raise ValueError(message)
        if self.kind is not SlackSelectKind.CONVERSATIONS and self.conversation_types:
            message = "conversation_types is only valid for Slack conversation selectors"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class SlackTextInput:
    """A plain-text input used inside a Slack modal."""

    action_id: str
    initial_value: str | None = None
    placeholder: SlackText | None = None
    multiline: bool = False
    minimum_length: int | None = None
    maximum_length: int | None = None


@dataclass(frozen=True, slots=True)
class SlackNumberInput:
    """A numeric input used inside a Slack modal."""

    action_id: str
    initial_value: str | None = None
    decimal_allowed: bool = False
    minimum: str | None = None
    maximum: str | None = None


@dataclass(frozen=True, slots=True)
class SlackDatePicker:
    """A calendar-date input used inside a Slack modal."""

    action_id: str
    initial_date: str | None = None
    placeholder: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackTimePicker:
    """A local-time input used inside a Slack modal."""

    action_id: str
    initial_time: str | None = None
    placeholder: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackCheckboxes:
    """A checkbox group used inside a Slack modal."""

    action_id: str
    options: tuple[SlackOption, ...]
    initial_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackRadioButtons:
    """A radio group used inside a Slack modal."""

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
    """A section block with optional fields and accessory."""

    text: SlackText | None = None
    fields: tuple[SlackText, ...] = ()
    accessory: SlackElement | None = None


@dataclass(frozen=True, slots=True)
class SlackHeader:
    """A plain-text header block."""

    text: SlackText


@dataclass(frozen=True, slots=True)
class SlackContext:
    """A context block containing text snippets."""

    elements: tuple[SlackText, ...]


@dataclass(frozen=True, slots=True)
class SlackDivider:
    """A visual divider block."""


@dataclass(frozen=True, slots=True)
class SlackImage:
    """An image block with alternative text."""

    image_url: str
    alt_text: str
    title: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackActions:
    """One row of Slack interactive elements."""

    elements: tuple[SlackButton | SlackSelect, ...]
    block_id: str | None = None


@dataclass(frozen=True, slots=True)
class SlackInput:
    """One labelled modal field with stable block and action identifiers."""

    block_id: str
    label: SlackText
    element: SlackInputElement
    optional: bool = False
    hint: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackTable:
    """A table block whose cells are already allocated Slack text objects."""

    rows: tuple[tuple[SlackText, ...], ...]


@dataclass(frozen=True, slots=True)
class SlackCard:
    """A current-generation Slack card block."""

    title: SlackText | None = None
    description: SlackText | None = None
    image_url: str | None = None
    actions: tuple[SlackButton, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackCarousel:
    """A carousel block containing up to ten cards."""

    cards: tuple[SlackCard, ...]


class SlackAlertStyle(StrEnum):
    """Visual tones supported by Slack alert blocks."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SlackAlert:
    """A modal alert block."""

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
    """A Block Kit message plus its screen-reader fallback text."""

    KIND = "slack_message"

    text: str
    blocks: tuple[SlackBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackModalView:
    """A complete Slack modal view."""

    KIND = "slack_modal"

    callback_id: str
    title: SlackText
    submit: SlackText
    close: SlackText
    blocks: tuple[SlackBlock, ...] = ()
    private_metadata: str | None = None


@dataclass(frozen=True, slots=True)
class SlackHomeView:
    """A complete Slack App Home view."""

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

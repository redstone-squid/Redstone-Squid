"""Describe a planned Slack message, modal, or App Home view for rendering.

Each body contains Block Kit blocks, which in turn contain text and interactive
elements. Actions and assets are references into PlanResult; these records contain
no callbacks or file bytes. The Slack renderer converts them to API request objects.
Planning and schema validation check most target limits, rather than these constructors.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from squid_ui.action_mode import ActionMode
from squid_ui.content.entity import ConversationType
from squid_ui.scene.base import Body, SceneNode


class SlackTextKind(StrEnum):
    """One Slack text-object representation."""

    PLAIN = "plain_text"
    MARKDOWN = "mrkdwn"


@dataclass(frozen=True, slots=True)
class SlackText(SceneNode):
    """Text resolved for one Slack text object."""

    content: str
    kind: SlackTextKind = SlackTextKind.MARKDOWN
    emoji: Annotated[bool, Field(strict=True)] | None = None
    verbatim: Annotated[bool, Field(strict=True)] | None = True


@dataclass(frozen=True, slots=True)
class SlackOption(SceneNode):
    """One static-select, radio, or checkbox option."""

    label: SlackText
    value: Annotated[str, Field(max_length=150)]
    description: SlackText | None = None


class SlackButtonStyle(StrEnum):
    """Styles Slack accepts on button elements."""

    DEFAULT = "default"
    PRIMARY = "primary"
    DANGER = "danger"


@dataclass(frozen=True, slots=True)
class SlackActionRef(SceneNode):
    """Reference to one planned action binding."""

    action: Annotated[str, Field(max_length=255)]
    mode: ActionMode = ActionMode.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class SlackRouteRef(SceneNode):
    """Reference to one host-owned stateless route."""

    route_id: Annotated[str, Field(max_length=255)]


@dataclass(frozen=True, slots=True)
class SlackAssetRef(SceneNode):
    """Reference to one delivery asset requiring a public URL at draw time."""

    key: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class SlackButton(SceneNode):
    """A button with exactly one destination: action, route, URL, or delivery asset."""

    label: SlackText
    action: SlackActionRef | None = None
    route: SlackRouteRef | None = None
    url: Annotated[str, Field(max_length=3000)] | None = None
    asset: SlackAssetRef | None = None
    value: Annotated[str, Field(max_length=2000)] | None = None
    style: SlackButtonStyle = SlackButtonStyle.DEFAULT

    def __post_init__(self) -> None:
        destinations = sum(value is not None for value in (self.action, self.route, self.url, self.asset))
        if destinations != 1:
            message = "Slack buttons require exactly one action, route, URL, or asset"
            raise ValueError(message)


class SlackSelectKind(StrEnum):
    """Native selector families exposed by Block Kit."""

    STATIC = "static"
    USERS = "users"
    CONVERSATIONS = "conversations"


@dataclass(frozen=True, slots=True)
class SlackSelect(SceneNode):
    """Let a reader choose declared options, Slack users, or Slack conversations.

    Exactly one action binding, route, or form action ID identifies where the selection
    goes. A form action ID identifies the field whose value is submitted with the form.
    Static options are allowed only for `STATIC`; conversation filters only for
    `CONVERSATIONS`. Other count and length limits are checked outside this model.
    """

    action: SlackActionRef | None = None
    route: SlackRouteRef | None = None
    action_id: Annotated[str, Field(max_length=255)] | None = None
    kind: SlackSelectKind = SlackSelectKind.STATIC
    placeholder: SlackText | None = None
    options: Annotated[tuple[SlackOption, ...], Field(max_length=100)] = ()
    initial_values: tuple[str, ...] = ()
    conversation_types: tuple[ConversationType, ...] = ()
    minimum: Annotated[int, Field(ge=0, strict=True)] = 1
    maximum: Annotated[int, Field(ge=1, strict=True)] = 1

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
class SlackTextInput(SceneNode):
    """A plain-text input used inside a Slack modal."""

    action_id: Annotated[str, Field(max_length=255)]
    initial_value: str | None = None
    placeholder: SlackText | None = None
    multiline: Annotated[bool, Field(strict=True)] = False
    minimum_length: Annotated[int, Field(ge=0, strict=True)] | None = None
    maximum_length: Annotated[int, Field(ge=0, strict=True)] | None = None


@dataclass(frozen=True, slots=True)
class SlackNumberInput(SceneNode):
    """A numeric input used inside a Slack modal."""

    action_id: Annotated[str, Field(max_length=255)]
    initial_value: str | None = None
    decimal_allowed: Annotated[bool, Field(strict=True)] = False
    minimum: str | None = None
    maximum: str | None = None


@dataclass(frozen=True, slots=True)
class SlackDatePicker(SceneNode):
    """A calendar-date input used inside a Slack modal."""

    action_id: Annotated[str, Field(max_length=255)]
    initial_date: str | None = None
    placeholder: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackTimePicker(SceneNode):
    """A local-time input used inside a Slack modal."""

    action_id: Annotated[str, Field(max_length=255)]
    initial_time: str | None = None
    placeholder: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackCheckboxes(SceneNode):
    """A checkbox group used inside a Slack modal."""

    action_id: Annotated[str, Field(max_length=255)]
    options: Annotated[tuple[SlackOption, ...], Field(max_length=10)]
    initial_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackRadioButtons(SceneNode):
    """A radio group used inside a Slack modal."""

    action_id: Annotated[str, Field(max_length=255)]
    options: Annotated[tuple[SlackOption, ...], Field(max_length=10)]
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
class SlackSection(SceneNode):
    """A section block with optional fields and accessory."""

    text: SlackText | None = None
    fields: Annotated[tuple[SlackText, ...], Field(max_length=10)] = ()
    accessory: SlackElement | None = None


@dataclass(frozen=True, slots=True)
class SlackHeader(SceneNode):
    """A plain-text header block."""

    text: SlackText


@dataclass(frozen=True, slots=True)
class SlackContext(SceneNode):
    """A context block containing text snippets."""

    elements: Annotated[tuple[SlackText, ...], Field(max_length=10)]


@dataclass(frozen=True, slots=True)
class SlackDivider(SceneNode):
    """A visual divider block."""


@dataclass(frozen=True, slots=True)
class SlackImage(SceneNode):
    """An image block with alternative text."""

    image_url: Annotated[str, Field(max_length=3000)]
    alt_text: Annotated[str, Field(max_length=2000)]
    title: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackActions(SceneNode):
    """One row of Slack interactive elements."""

    elements: Annotated[tuple[SlackButton | SlackSelect, ...], Field(max_length=25)]
    block_id: Annotated[str, Field(max_length=255)] | None = None


@dataclass(frozen=True, slots=True)
class SlackInput(SceneNode):
    """One labelled modal field with stable block and action identifiers."""

    block_id: Annotated[str, Field(max_length=255)]
    label: SlackText
    element: SlackInputElement
    optional: Annotated[bool, Field(strict=True)] = False
    hint: SlackText | None = None


@dataclass(frozen=True, slots=True)
class SlackTable(SceneNode):
    """A table block whose cells are already allocated Slack text objects."""

    rows: Annotated[tuple[Annotated[tuple[SlackText, ...], Field(max_length=20)], ...], Field(max_length=100)]


@dataclass(frozen=True, slots=True)
class SlackCard(SceneNode):
    """A Slack card with optional title, description, image, and button actions."""

    title: SlackText | None = None
    description: SlackText | None = None
    image_url: Annotated[str, Field(max_length=3000)] | None = None
    actions: tuple[SlackButton, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackCarousel(SceneNode):
    """A carousel block containing up to ten cards."""

    cards: Annotated[tuple[SlackCard, ...], Field(max_length=10)]


class SlackAlertStyle(StrEnum):
    """Visual tones supported by Slack alert blocks."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SlackAlert(SceneNode):
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
class SlackMessage(Body):
    """A Block Kit message plus its screen-reader fallback text."""

    KIND = "slack_message"

    text: Annotated[str, Field(max_length=40000)]
    blocks: Annotated[tuple[SlackBlock, ...], Field(max_length=50)] = ()


@dataclass(frozen=True, slots=True)
class SlackModalView(Body):
    """A complete Slack modal view."""

    KIND = "slack_modal"

    callback_id: Annotated[str, Field(max_length=255)]
    title: SlackText
    submit: SlackText
    close: SlackText
    blocks: Annotated[tuple[SlackBlock, ...], Field(max_length=100)] = ()
    private_metadata: Annotated[str, Field(max_length=3000)] | None = None


@dataclass(frozen=True, slots=True)
class SlackHomeView(Body):
    """A complete Slack App Home view."""

    KIND = "slack_home"

    blocks: Annotated[tuple[SlackBlock, ...], Field(max_length=100)] = ()
    callback_id: Annotated[str, Field(max_length=255)] | None = None
    private_metadata: Annotated[str, Field(max_length=3000)] | None = None


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


SlackMessage.DISPLAY_FIELDS = ("blocks",)
SlackModalView.DISPLAY_FIELDS = ("title", "blocks", "close", "submit")
SlackHomeView.DISPLAY_FIELDS = ("blocks",)
SlackSection.DISPLAY_FIELDS = ("text", "fields", "accessory")
SlackHeader.DISPLAY_FIELDS = ("text",)
SlackContext.DISPLAY_FIELDS = ("elements",)
SlackDivider.DISPLAY_FIELDS = ()
SlackImage.DISPLAY_FIELDS = ("title",)
SlackActions.DISPLAY_FIELDS = ("elements",)
SlackInput.DISPLAY_FIELDS = ("label", "element", "hint")
SlackTable.DISPLAY_FIELDS = ("rows",)
SlackCard.DISPLAY_FIELDS = ("title", "description", "actions")
SlackCarousel.DISPLAY_FIELDS = ("cards",)
SlackAlert.DISPLAY_FIELDS = ("title", "text")
SlackText.DISPLAY_FIELDS = ()
SlackOption.DISPLAY_FIELDS = ("label", "description")
SlackButton.DISPLAY_FIELDS = ("label",)
SlackSelect.DISPLAY_FIELDS = ("placeholder", "options")
SlackTextInput.DISPLAY_FIELDS = ("placeholder",)
SlackNumberInput.DISPLAY_FIELDS = ()
SlackDatePicker.DISPLAY_FIELDS = ("placeholder",)
SlackTimePicker.DISPLAY_FIELDS = ("placeholder",)
SlackCheckboxes.DISPLAY_FIELDS = ("options",)
SlackRadioButtons.DISPLAY_FIELDS = ("options",)

SlackRouteRef.ROUTE_FIELDS = ("route_id",)

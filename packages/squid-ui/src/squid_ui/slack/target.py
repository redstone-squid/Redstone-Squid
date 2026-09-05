"""Slack Block Kit target identities and hard limits."""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, Self

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import Target
from squid_ui.target_types import SlackAdapter, SlackHomeTarget, SlackMessageTarget, SlackModalTarget


@dataclass(frozen=True, slots=True)
class SlackComponentLimits:
    """Per-block Block Kit caps, the same on every surface; the planner cuts to these and the renderer audits them."""

    action_id: int = 255
    block_id: int = 255
    actions_elements: int = 25
    button_label: int = 75
    button_value: int = 2000
    url: int = 3000
    header_text: int = 150
    section_text: int = 3000
    section_fields: int = 10
    section_field_text: int = 2000
    context_elements: int = 10
    option_label: int = 75
    option_description: int = 75
    option_value: int = 150
    select_options: int = 100
    """Options in one static select; the planner keeps the first this many."""
    choice_options: int = 10
    """Options in one radio or checkbox group."""
    placeholder: int = 150
    table_rows: int = 100
    table_columns: int = 20
    table_text: int = 10000
    """Characters across every cell of one table, not per cell."""
    card_title: int = 150
    card_body: int = 200
    carousel_cards: int = 10
    alert_text: int = 200


SLACK_COMPONENT_LIMITS = SlackComponentLimits()


def _limit_values(value: Any, prefix: str = "") -> tuple[tuple[str, object], ...]:
    pairs: list[tuple[str, object]] = []
    for held in fields(value):
        item = getattr(value, held.name)
        name = f"{prefix}{held.name}"
        if is_dataclass(item) and not isinstance(item, type):
            pairs.extend(_limit_values(item, f"{name}."))
        else:
            pairs.append((name, item))
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class SlackLimits:
    """Limits for one Slack surface; `blocks` is the only reservable axis (`Axis.BLOCKS`)."""

    blocks: int
    """Top-level blocks per payload: 50 for a message, 100 for a view."""
    components: SlackComponentLimits = SLACK_COMPONENT_LIMITS

    @property
    def capacities(self) -> Mapping[Axis, int]:
        return {Axis.BLOCKS: self.blocks}

    def with_capacities(self, reductions: Mapping[Axis, int]) -> Self:
        return replace(self, blocks=max(0, self.blocks - reductions.get(Axis.BLOCKS, 0)))

    def digest(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(_limit_values(self)))


@dataclass(frozen=True, slots=True)
class SlackMessageLimits(SlackLimits):
    """Limits for one Slack message; also caps the plain-text `text` field the planner derives from the document."""

    blocks: int = 50
    fallback_text: int = 40000
    """Hard cap on the message's plain-text `text`; the planner truncates to it."""
    recommended_fallback_text: int = 4000
    """Slack's advisory cap for `text`; nothing enforces it."""


@dataclass(frozen=True, slots=True)
class SlackModalLimits(SlackLimits):
    """Limits for one modal view; `title`, `submit` and `close` cap the view's own button and title text."""

    blocks: int = 100
    title: int = 24
    submit: int = 24
    close: int = 24
    callback_id: int = 255
    """Cap on the modal form's key, which becomes the view `callback_id`."""
    private_metadata: int = 3000


@dataclass(frozen=True, slots=True)
class SlackHomeLimits(SlackLimits):
    """Limits for one App Home view: 100 blocks and the view-level ids, with no form or title text."""

    blocks: int = 100
    callback_id: int = 255
    private_metadata: int = 3000


SLACK_MESSAGE_LIMITS = SlackMessageLimits()
SLACK_MODAL_LIMITS = SlackModalLimits()
SLACK_HOME_LIMITS = SlackHomeLimits()


_COMMON_CAPABILITIES = frozenset(
    {
        Capability.ACTIONS_BUTTONS,
        Capability.ACTIONS_ENTITY,
        Capability.ACTIONS_SELECT,
        Capability.LAYOUT_CARD,
        Capability.LAYOUT_CONTAINER,
        Capability.LAYOUT_SECTION,
        Capability.LAYOUT_SEMANTIC,
    }
)


class SlackMessageDialect:
    """`slack.block-kit.message`: carousels, galleries and tables, but no forms or alerts."""

    id = "slack.block-kit.message"
    version = 1
    capabilities = _COMMON_CAPABILITIES | frozenset(
        {Capability.LAYOUT_CAROUSEL, Capability.LAYOUT_GALLERY, Capability.LAYOUT_TABLE}
    )
    render_target = SlackMessageTarget
    body_type = scene.SlackMessage
    default_limits = SLACK_MESSAGE_LIMITS
    realizes_extensions = False

    @property
    def planner(self) -> Any:
        from squid_ui.planning.slack_planner import SLACK_PLANNER

        return SLACK_PLANNER


class SlackModalDialect:
    """`slack.block-kit.modal`: modal forms and alerts, but no carousel, gallery or table blocks."""

    id = "slack.block-kit.modal"
    version = 1
    capabilities = _COMMON_CAPABILITIES | frozenset({Capability.FORMS_MODAL, Capability.LAYOUT_ALERT})
    render_target = SlackModalTarget
    body_type = scene.SlackModalView
    default_limits = SLACK_MODAL_LIMITS
    realizes_extensions = False

    @property
    def planner(self) -> Any:
        from squid_ui.planning.slack_planner import SLACK_PLANNER

        return SLACK_PLANNER


class SlackHomeDialect:
    """`slack.block-kit.home`: the message dialect's block set on a 100-block view with no forms."""

    id = "slack.block-kit.home"
    version = 1
    capabilities = _COMMON_CAPABILITIES | frozenset(
        {Capability.LAYOUT_CAROUSEL, Capability.LAYOUT_GALLERY, Capability.LAYOUT_TABLE}
    )
    render_target = SlackHomeTarget
    body_type = scene.SlackHomeView
    default_limits = SLACK_HOME_LIMITS
    realizes_extensions = False

    @property
    def planner(self) -> Any:
        from squid_ui.planning.slack_planner import SLACK_PLANNER

        return SLACK_PLANNER


SLACK_MESSAGE_DIALECT = SlackMessageDialect()
SLACK_MODAL_DIALECT = SlackModalDialect()
SLACK_HOME_DIALECT = SlackHomeDialect()


def message_target[AdapterT: SlackAdapter](
    *, adapter: AdapterProfile[AdapterT], limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS
) -> Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, AdapterT]:
    """A target for `SLACK_MESSAGE_DIALECT`; `limits` enters the target fingerprint, so custom caps plan apart."""
    return Target(SLACK_MESSAGE_DIALECT, adapter, limits)


def modal_target[AdapterT: SlackAdapter](
    *, adapter: AdapterProfile[AdapterT], limits: SlackModalLimits = SLACK_MODAL_LIMITS
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, AdapterT]:
    """A target for `SLACK_MODAL_DIALECT`; `limits` enters the target fingerprint, so custom caps plan apart."""
    return Target(SLACK_MODAL_DIALECT, adapter, limits)


def home_target[AdapterT: SlackAdapter](
    *, adapter: AdapterProfile[AdapterT], limits: SlackHomeLimits = SLACK_HOME_LIMITS
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, AdapterT]:
    """A target for `SLACK_HOME_DIALECT`; `limits` enters the target fingerprint, so custom caps plan apart."""
    return Target(SLACK_HOME_DIALECT, adapter, limits)


__all__ = [
    "SLACK_COMPONENT_LIMITS",
    "SLACK_HOME_DIALECT",
    "SLACK_HOME_LIMITS",
    "SLACK_MESSAGE_DIALECT",
    "SLACK_MESSAGE_LIMITS",
    "SLACK_MODAL_DIALECT",
    "SLACK_MODAL_LIMITS",
    "SlackComponentLimits",
    "SlackHomeDialect",
    "SlackHomeLimits",
    "SlackLimits",
    "SlackMessageDialect",
    "SlackMessageLimits",
    "SlackModalDialect",
    "SlackModalLimits",
    "home_target",
    "message_target",
    "modal_target",
]

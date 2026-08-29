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
    """Local Block Kit caps shared by messages and views."""

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
    choice_options: int = 10
    placeholder: int = 150
    table_rows: int = 100
    table_columns: int = 20
    table_text: int = 10000
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
    """Common target-wide and local limits for one Slack surface."""

    blocks: int
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
    """Hard limits for one Slack message."""

    blocks: int = 50
    fallback_text: int = 40000
    recommended_fallback_text: int = 4000


@dataclass(frozen=True, slots=True)
class SlackModalLimits(SlackLimits):
    """Hard limits for one Slack modal view."""

    blocks: int = 100
    title: int = 24
    submit: int = 24
    close: int = 24
    callback_id: int = 255
    private_metadata: int = 3000


@dataclass(frozen=True, slots=True)
class SlackHomeLimits(SlackLimits):
    """Hard limits for one Slack App Home view."""

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
    """Slack Block Kit message scene and planner contract."""

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
    """Slack Block Kit modal scene and planner contract."""

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
    """Slack App Home scene and planner contract."""

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
    """Return a Slack message target realized by ``adapter``."""
    return Target(SLACK_MESSAGE_DIALECT, adapter, limits)


def modal_target[AdapterT: SlackAdapter](
    *, adapter: AdapterProfile[AdapterT], limits: SlackModalLimits = SLACK_MODAL_LIMITS
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, AdapterT]:
    """Return a Slack modal target realized by ``adapter``."""
    return Target(SLACK_MODAL_DIALECT, adapter, limits)


def home_target[AdapterT: SlackAdapter](
    *, adapter: AdapterProfile[AdapterT], limits: SlackHomeLimits = SLACK_HOME_LIMITS
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, AdapterT]:
    """Return a Slack App Home target realized by ``adapter``."""
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

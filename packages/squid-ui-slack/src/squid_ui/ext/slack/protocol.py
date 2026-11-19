"""Slack Block Kit target identities and hard limits."""

from squid_ui.authoring.document import DocumentLike
from squid_ui.capabilities import Capability
from squid_ui.ext.slack import scene
from squid_ui.ext.slack._forms import modal_form
from squid_ui.ext.slack.composition import SlackCompositionRules
from squid_ui.ext.slack.limits import (
    SLACK_COMPONENT_LIMITS as SLACK_COMPONENT_LIMITS,
)
from squid_ui.ext.slack.limits import (
    SLACK_HOME_LIMITS as SLACK_HOME_LIMITS,
)
from squid_ui.ext.slack.limits import (
    SLACK_MESSAGE_LIMITS as SLACK_MESSAGE_LIMITS,
)
from squid_ui.ext.slack.limits import (
    SLACK_MODAL_LIMITS as SLACK_MODAL_LIMITS,
)
from squid_ui.ext.slack.limits import (
    SlackComponentLimits as SlackComponentLimits,
)
from squid_ui.ext.slack.limits import (
    SlackHomeLimits as SlackHomeLimits,
)
from squid_ui.ext.slack.limits import (
    SlackLimits as SlackLimits,
)
from squid_ui.ext.slack.limits import (
    SlackMessageLimits as SlackMessageLimits,
)
from squid_ui.ext.slack.limits import (
    SlackModalLimits as SlackModalLimits,
)
from squid_ui.ext.slack.target_types import SlackAdapter, SlackHomeTarget, SlackMessageTarget, SlackModalTarget
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.composition_planner import RuleComposition
from squid_ui.planning.dialect import InputPolicy, TargetComposition
from squid_ui.planning.pagination.navigation import PlannedNav, default_nav
from squid_ui.planning.target import Target

_COMMON_CAPABILITIES = frozenset(
    {
        Capability.ACTIONS_BUTTONS,
        Capability.ACTIONS_ENTITY,
        Capability.ACTIONS_SELECT,
        Capability.FORMS_MODAL,
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
    def navigation(self) -> PlannedNav | None:
        return default_nav

    def input_policy(self, limits: SlackMessageLimits) -> InputPolicy:
        return InputPolicy(limits.components.section_text)

    def composer[R](
        self, document: DocumentLike[R], limits: SlackMessageLimits
    ) -> TargetComposition[R, scene.SlackMessage]:
        return RuleComposition(SlackCompositionRules[R, object, scene.SlackMessage](limits))


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
    def navigation(self) -> PlannedNav | None:
        return None

    def input_policy(self, limits: SlackModalLimits) -> InputPolicy:
        return InputPolicy(inspect=False)

    def composer[R](
        self, document: DocumentLike[R], limits: SlackModalLimits
    ) -> TargetComposition[R, scene.SlackModalView]:
        return RuleComposition(SlackCompositionRules[R, object, scene.SlackModalView](limits, modal_form(document)))


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
    def navigation(self) -> PlannedNav | None:
        return default_nav

    def input_policy(self, limits: SlackHomeLimits) -> InputPolicy:
        return InputPolicy(limits.components.section_text)

    def composer[R](
        self, document: DocumentLike[R], limits: SlackHomeLimits
    ) -> TargetComposition[R, scene.SlackHomeView]:
        return RuleComposition(SlackCompositionRules[R, object, scene.SlackHomeView](limits))


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

"""Turn Squid plans into Slack Block Kit messages, modals, and App Home views.

Choose a target for the intended surface, plan the document with squid_ui.planning,
then pass the resulting scene to its renderer. MessageRenderer returns a MessagePayload;
ModalRenderer and HomeRenderer return Slack SDK View objects. The application sends
those values and dispatches incoming action IDs using the matching plan's bindings.
"""

from squid_ui.ext.slack import adapter, message_payload, renderer, target
from squid_ui.ext.slack.adapter import (
    SLACK_SDK_343_ADAPTER,
    SLACK_SDK_BEHAVIOR_CAPABILITIES,
    slack_sdk_adapter_profile,
)
from squid_ui.ext.slack.message_payload import MessagePayload
from squid_ui.ext.slack.renderer import AssetResolver, HomeRenderer, MessageRenderer, ModalRenderer
from squid_ui.ext.slack.target import (
    SLACK_HOME_SDK343,
    SLACK_MESSAGE_SDK343,
    SLACK_MODAL_SDK343,
    home,
    message,
    modal,
)

__all__ = [
    "SLACK_HOME_SDK343",
    "SLACK_MESSAGE_SDK343",
    "SLACK_MODAL_SDK343",
    "SLACK_SDK_343_ADAPTER",
    "SLACK_SDK_BEHAVIOR_CAPABILITIES",
    "AssetResolver",
    "HomeRenderer",
    "MessagePayload",
    "MessageRenderer",
    "ModalRenderer",
    "adapter",
    "home",
    "message",
    "message_payload",
    "modal",
    "renderer",
    "slack_sdk_adapter_profile",
    "target",
]

from squid_ui.ext.slack.protocol import SLACK_COMPONENT_LIMITS as SLACK_COMPONENT_LIMITS
from squid_ui.ext.slack.protocol import SLACK_HOME_DIALECT as SLACK_HOME_DIALECT
from squid_ui.ext.slack.protocol import SLACK_HOME_LIMITS as SLACK_HOME_LIMITS
from squid_ui.ext.slack.protocol import SLACK_MESSAGE_DIALECT as SLACK_MESSAGE_DIALECT
from squid_ui.ext.slack.protocol import SLACK_MESSAGE_LIMITS as SLACK_MESSAGE_LIMITS
from squid_ui.ext.slack.protocol import SLACK_MODAL_DIALECT as SLACK_MODAL_DIALECT
from squid_ui.ext.slack.protocol import SLACK_MODAL_LIMITS as SLACK_MODAL_LIMITS
from squid_ui.ext.slack.protocol import SlackComponentLimits as SlackComponentLimits
from squid_ui.ext.slack.protocol import SlackHomeDialect as SlackHomeDialect
from squid_ui.ext.slack.protocol import SlackHomeLimits as SlackHomeLimits
from squid_ui.ext.slack.protocol import SlackLimits as SlackLimits
from squid_ui.ext.slack.protocol import SlackMessageDialect as SlackMessageDialect
from squid_ui.ext.slack.protocol import SlackMessageLimits as SlackMessageLimits
from squid_ui.ext.slack.protocol import SlackModalDialect as SlackModalDialect
from squid_ui.ext.slack.protocol import SlackModalLimits as SlackModalLimits
from squid_ui.ext.slack.protocol import home_target as home_target
from squid_ui.ext.slack.protocol import message_target as message_target
from squid_ui.ext.slack.protocol import modal_target as modal_target
from squid_ui.ext.slack.target_types import SlackAdapter as SlackAdapter
from squid_ui.ext.slack.target_types import SlackHomeTarget as SlackHomeTarget
from squid_ui.ext.slack.target_types import SlackMessageTarget as SlackMessageTarget
from squid_ui.ext.slack.target_types import SlackModalTarget as SlackModalTarget
from squid_ui.ext.slack.target_types import SlackSdk343Adapter as SlackSdk343Adapter
from squid_ui.ext.slack.target_types import SlackSdkAdapter as SlackSdkAdapter
from squid_ui.ext.slack.target_types import SlackTarget as SlackTarget

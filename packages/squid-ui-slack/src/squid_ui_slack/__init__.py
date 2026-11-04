"""Turn Squid plans into Slack Block Kit messages, modals, and App Home views.

Choose a target for the intended surface, plan the document with squid_ui.planning,
then pass the resulting scene to its renderer. MessageRenderer returns a MessagePayload;
ModalRenderer and HomeRenderer return Slack SDK View objects. The application sends
those values and dispatches incoming action IDs using the matching plan's bindings.
"""

from squid_ui_slack import adapter, message_payload, renderer, target
from squid_ui_slack.adapter import (
    SLACK_SDK_343_ADAPTER,
    SLACK_SDK_BEHAVIOR_CAPABILITIES,
    slack_sdk_adapter_profile,
)
from squid_ui_slack.message_payload import MessagePayload
from squid_ui_slack.renderer import AssetResolver, HomeRenderer, MessageRenderer, ModalRenderer
from squid_ui_slack.target import (
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

"""Slack Block Kit targets bound to the verified Slack SDK adapter."""

from typing import overload

from squid_ui import scene
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.slack.target import (
    SLACK_HOME_LIMITS,
    SLACK_MESSAGE_LIMITS,
    SLACK_MODAL_LIMITS,
    SlackHomeLimits,
    SlackMessageLimits,
    SlackModalLimits,
    home_target,
    message_target,
    modal_target,
)
from squid_ui.target_types import (
    SlackHomeTarget,
    SlackMessageTarget,
    SlackModalTarget,
    SlackSdk343Adapter,
    SlackSdkAdapter,
)
from squid_ui_slack.adapter import SLACK_SDK_343_ADAPTER


@overload
def message(
    *, limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS
) -> Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, SlackSdk343Adapter]: ...


@overload
def message[ProfileT: SlackSdkAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS
) -> Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, ProfileT]: ...


def message(
    *,
    adapter: AdapterProfile[SlackSdkAdapter] = SLACK_SDK_343_ADAPTER,
    limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS,
) -> Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, SlackSdkAdapter]:
    """`Target` a `MessageRenderer` draws; omit `adapter` and it is typed for `SlackSdk343Adapter`.

    Pass a `slack_sdk_adapter_profile` to plan against another verified `slack-sdk` range.
    """
    return message_target(adapter=adapter, limits=limits)


@overload
def modal(
    *, limits: SlackModalLimits = SLACK_MODAL_LIMITS
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, SlackSdk343Adapter]: ...


@overload
def modal[ProfileT: SlackSdkAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: SlackModalLimits = SLACK_MODAL_LIMITS
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, ProfileT]: ...


def modal(
    *,
    adapter: AdapterProfile[SlackSdkAdapter] = SLACK_SDK_343_ADAPTER,
    limits: SlackModalLimits = SLACK_MODAL_LIMITS,
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, SlackSdkAdapter]:
    """`Target` a `ModalRenderer` draws; omit `adapter` and it is typed for `SlackSdk343Adapter`.

    Pass a `slack_sdk_adapter_profile` to plan against another verified `slack-sdk` range.
    """
    return modal_target(adapter=adapter, limits=limits)


@overload
def home(
    *, limits: SlackHomeLimits = SLACK_HOME_LIMITS
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, SlackSdk343Adapter]: ...


@overload
def home[ProfileT: SlackSdkAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: SlackHomeLimits = SLACK_HOME_LIMITS
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, ProfileT]: ...


def home(
    *,
    adapter: AdapterProfile[SlackSdkAdapter] = SLACK_SDK_343_ADAPTER,
    limits: SlackHomeLimits = SLACK_HOME_LIMITS,
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, SlackSdkAdapter]:
    """`Target` a `HomeRenderer` draws; omit `adapter` and it is typed for `SlackSdk343Adapter`.

    Pass a `slack_sdk_adapter_profile` to plan against another verified `slack-sdk` range.
    """
    return home_target(adapter=adapter, limits=limits)


SLACK_MESSAGE_SDK343 = message()
"""`message()` with every default: Slack's published message limits over `slack-sdk` 3.43."""

SLACK_MODAL_SDK343 = modal()
"""`modal()` with every default: Slack's published modal limits over `slack-sdk` 3.43."""

SLACK_HOME_SDK343 = home()
"""`home()` with every default: Slack's published App Home limits over `slack-sdk` 3.43."""


__all__ = [
    "SLACK_HOME_SDK343",
    "SLACK_MESSAGE_SDK343",
    "SLACK_MODAL_SDK343",
    "home",
    "message",
    "modal",
]

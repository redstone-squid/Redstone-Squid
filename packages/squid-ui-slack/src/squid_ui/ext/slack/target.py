"""Slack Block Kit targets bound to the verified Slack SDK adapter."""

from typing import overload

from squid_ui.ext.slack import scene
from squid_ui.ext.slack.adapter import SLACK_SDK_343_ADAPTER
from squid_ui.ext.slack.protocol import (
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
from squid_ui.ext.slack.target_types import (
    SlackHomeTarget,
    SlackMessageTarget,
    SlackModalTarget,
    SlackSdk343Adapter,
    SlackSdkAdapter,
)
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target


@overload
def message(
    *, limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS
) -> Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, SlackSdk343Adapter]: ...


@overload
def message[ProfileT: SlackSdkAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS
) -> Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, ProfileT]: ...


def message[ProfileT: SlackSdkAdapter](
    *,
    adapter: AdapterProfile[ProfileT] | None = None,
    limits: SlackMessageLimits = SLACK_MESSAGE_LIMITS,
) -> (
    Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, ProfileT]
    | Target[SlackMessageLimits, scene.SlackMessage, SlackMessageTarget, SlackSdk343Adapter]
):
    """A Slack Block Kit message target realized by the Slack SDK."""
    if adapter is None:
        return message_target(adapter=SLACK_SDK_343_ADAPTER, limits=limits)
    return message_target(adapter=adapter, limits=limits)


@overload
def modal(
    *, limits: SlackModalLimits = SLACK_MODAL_LIMITS
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, SlackSdk343Adapter]: ...


@overload
def modal[ProfileT: SlackSdkAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: SlackModalLimits = SLACK_MODAL_LIMITS
) -> Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, ProfileT]: ...


def modal[ProfileT: SlackSdkAdapter](
    *,
    adapter: AdapterProfile[ProfileT] | None = None,
    limits: SlackModalLimits = SLACK_MODAL_LIMITS,
) -> (
    Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, ProfileT]
    | Target[SlackModalLimits, scene.SlackModalView, SlackModalTarget, SlackSdk343Adapter]
):
    """A Slack Block Kit modal target realized by the Slack SDK."""
    if adapter is None:
        return modal_target(adapter=SLACK_SDK_343_ADAPTER, limits=limits)
    return modal_target(adapter=adapter, limits=limits)


@overload
def home(
    *, limits: SlackHomeLimits = SLACK_HOME_LIMITS
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, SlackSdk343Adapter]: ...


@overload
def home[ProfileT: SlackSdkAdapter](
    *, adapter: AdapterProfile[ProfileT], limits: SlackHomeLimits = SLACK_HOME_LIMITS
) -> Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, ProfileT]: ...


def home[ProfileT: SlackSdkAdapter](
    *,
    adapter: AdapterProfile[ProfileT] | None = None,
    limits: SlackHomeLimits = SLACK_HOME_LIMITS,
) -> (
    Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, ProfileT]
    | Target[SlackHomeLimits, scene.SlackHomeView, SlackHomeTarget, SlackSdk343Adapter]
):
    """A Slack App Home target realized by the Slack SDK."""
    if adapter is None:
        return home_target(adapter=SLACK_SDK_343_ADAPTER, limits=limits)
    return home_target(adapter=adapter, limits=limits)


SLACK_MESSAGE_SDK343 = message()
"""The default Slack message target: Block Kit over Slack SDK 3.43."""

SLACK_MODAL_SDK343 = modal()
"""The default Slack modal target: Block Kit over Slack SDK 3.43."""

SLACK_HOME_SDK343 = home()
"""The default Slack App Home target: Block Kit over Slack SDK 3.43."""


__all__ = [
    "SLACK_HOME_SDK343",
    "SLACK_MESSAGE_SDK343",
    "SLACK_MODAL_SDK343",
    "home",
    "message",
    "modal",
]

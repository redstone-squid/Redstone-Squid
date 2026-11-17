"""Slack protocol and renderer behavior declarations."""

from enum import StrEnum


class SlackAdapterCapability(StrEnum):
    """Behavior verified by this target's renderer adapters."""

    RENDER_SLACK_HOME = "adapter.slack.render.home"
    RENDER_SLACK_MESSAGE = "adapter.slack.render.message"
    RENDER_SLACK_MODAL = "adapter.slack.render.modal"

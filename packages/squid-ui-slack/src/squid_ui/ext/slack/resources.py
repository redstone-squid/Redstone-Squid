"""Slack-specific resource dimensions."""

from squid_ui.planning.resources import Axis


class SlackAxis:
    """Block Kit quantities measured by Slack composition."""

    BLOCKS = Axis("blocks")

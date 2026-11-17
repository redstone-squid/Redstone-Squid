"""Static target and adapter vocabulary for Slack."""

from squid_ui.target_types import RenderTarget


class SlackTarget(RenderTarget):
    """Marker for values renderable to any Slack Block Kit surface."""


class SlackMessageTarget(SlackTarget):
    """Marker for Slack message renderables."""


class SlackModalTarget(SlackTarget):
    """Marker for Slack modal renderables."""


class SlackHomeTarget(SlackTarget):
    """Marker for Slack App Home renderables."""


class SlackAdapter:
    """Marker for adapters capable of realizing Slack Block Kit targets."""


class SlackSdkAdapter(SlackAdapter):
    """Marker for adapters implemented with the Slack Python SDK."""


class SlackSdk343Adapter(SlackSdkAdapter):
    """Marker for Squid's verified Slack Python SDK 3.43 adapter."""

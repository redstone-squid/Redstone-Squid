"""Discord-specific resource dimensions."""

from squid_ui.planning.resources import Axis


class DiscordAxis:
    """Native message content and embed resources."""

    CONTENT_TEXT = Axis("content_text")
    EMBED_TEXT = Axis("embed_text")
    EMBEDS = Axis("embeds")


TEXT_AXES = frozenset({Axis.DISPLAY_TEXT, DiscordAxis.CONTENT_TEXT, DiscordAxis.EMBED_TEXT})

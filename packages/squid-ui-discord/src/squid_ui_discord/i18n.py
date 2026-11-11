"""Fluent declarations for Discord command metadata."""

from discord import app_commands

from squid_i18n import Markup, Message, source_text


def to_locale_str(message: Message) -> app_commands.locale_str:
    """Attach a parameter-free Fluent declaration to Discord command metadata."""
    if message.declaration is None or message.bindings:
        detail = "Discord command metadata requires a parameter-free Fluent declaration"
        raise ValueError(detail)
    return app_commands.locale_str(source_text(message, markup=Markup.PLAIN), fluent_message=message)

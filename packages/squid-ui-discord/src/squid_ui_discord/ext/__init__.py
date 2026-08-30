"""Composable discord.py integration for the explicit Squid facade."""

from squid_ui_discord.ext import testing
from squid_ui_discord.ext.cog import Cog
from squid_ui_discord.ext.commands import autocomplete, command, context_menu
from squid_ui_discord.facade import DiscordUI
from squid_ui_discord.request import AcknowledgementPolicy, DiscordRequest
from squid_ui_discord.response import (
    Abandoned,
    Presented,
    Rejected,
    Response,
    ResponseResult,
    ResponseSpec,
    Sent,
)
from squid_ui_discord.screen import Screen

__all__ = [
    "Abandoned",
    "AcknowledgementPolicy",
    "Cog",
    "DiscordRequest",
    "DiscordUI",
    "Presented",
    "Rejected",
    "Response",
    "ResponseResult",
    "ResponseSpec",
    "Screen",
    "Sent",
    "autocomplete",
    "command",
    "context_menu",
    "testing",
]

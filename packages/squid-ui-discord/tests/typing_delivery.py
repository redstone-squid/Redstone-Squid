"""Pins the delivery surfaces a host reaches for, under the project type check.

Never imported at runtime: these are assertions the type checker makes, and the point of
each one is that the host had to write no cast to get here.
"""

from typing import Any

import discord

from squid_ui_discord import Destination, deliver_to, edit_to, send_to
from squid_ui_discord.delivery import Messageable, Replyable


def channel_send(channel: discord.TextChannel) -> Destination:
    """A real discord.py channel, whose overloaded `send` the structural protocol misses."""
    return send_to(channel)


def thread_send(thread: discord.Thread) -> Destination:
    return send_to(thread)


def member_send(member: discord.Member, user: discord.User) -> tuple[Destination, Destination]:
    """Both are already `abc.Messageable`, so neither needs a special case."""
    return send_to(member), send_to(user)


def double_send(double: Messageable) -> Destination:
    """The structural overload stays, so a test double still delivers."""
    return send_to(double)


def edit(message: discord.Message) -> Destination:
    return edit_to(message)


def either_surface(interaction: discord.Interaction[Any], context: Replyable) -> tuple[Destination, Destination]:
    return deliver_to(interaction), deliver_to(context)

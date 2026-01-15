"""Discord embed utilities and messaging helpers."""

from traceback import format_tb
from types import TracebackType

import discord
from discord import Message, Webhook
from discord.abc import Messageable

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


def error_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_red, description=":x: " + description)


def warning_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=":warning: " + title, colour=discord_yellow, description=description)


def info_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title: str, description: str | None):
    if description is None:
        description = ""
    return discord.Embed(title=title, colour=discord_green, description=description)


class RunningMessage:
    """Context manager to show a working message while the bot is working."""

    def __init__(
        self,
        ctx: Messageable | Webhook,
        *,
        title: str = "Working",
        description: str = "Getting information...",
        delete_on_exit: bool = False,
        print_tracebacks: bool = False,  # Whether to print tracebacks in the message
        id_to_mention_on_error: int | None = None,
    ):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.delete_on_exit = delete_on_exit
        self.sent_message: Message
        self.print_tracebacks = print_tracebacks
        self.id_to_mention_on_error = id_to_mention_on_error

    async def __aenter__(self) -> Message:
        sent_message = await self.ctx.send(embed=info_embed(self.title, self.description))
        if sent_message is None:
            msg = "Failed to send message. (You are probably sending a message to a webhook, try looking into Webhook.send)"
            raise ValueError(msg)

        self.sent_message = sent_message
        return sent_message

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> bool:
        # Handle exceptions
        if exc_type is not None:
            description = f"{exc_val!s}"
            if self.print_tracebacks:
                description += f"\n\n```{''.join(format_tb(exc_tb))}```"
            await self.sent_message.edit(
                content=f"<@{self.id_to_mention_on_error}>" if self.id_to_mention_on_error else None,
                embed=error_embed(f"An error has occurred: {exc_type.__name__}", description),
            )
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False

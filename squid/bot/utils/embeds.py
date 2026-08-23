"""Components V2 progress-message context."""

from types import TracebackType

from discord import Message, Webhook
from discord.abc import Messageable

import squid_layouts as sl
from squid.bot.ui import info_layout
from squid.core.i18n import _, translate


class RunningMessage:
    """Context manager to show a working message while the bot is working."""

    def __init__(
        self,
        ctx: Messageable | Webhook,
        *,
        title: str = _("Working"),
        description: str = _("Getting information..."),
        delete_on_exit: bool = False,
        locale: str | None = None,
    ):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.delete_on_exit = delete_on_exit
        self.locale = locale
        self.sent_message: Message

    async def __aenter__(self) -> Message:
        receipt = await sl.discord.delivery.send_to(self.ctx)(
            info_layout(translate(self.locale, self.title), translate(self.locale, self.description))
        )
        sent_message = receipt.message
        if sent_message is None:
            msg = "Failed to send message. (You are probably sending a message to a webhook, try looking into Webhook.send)"
            raise ValueError(msg)

        self.sent_message = sent_message
        return sent_message

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> bool:
        # Handle exceptions
        if exc_val is not None:
            from squid.bot.errors import handle_message_error

            await handle_message_error(self.sent_message, exc_val, locale=self.locale)
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False

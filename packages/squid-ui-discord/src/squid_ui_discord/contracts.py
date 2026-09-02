"""Static contracts shared by the owner-scoped Discord facade."""

from collections.abc import Awaitable, Callable, Sequence

import discord
from discord.ext import commands

from squid_ui.document import Document
from squid_ui.runtime.component import Component
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import ComponentsV2Target
from squid_ui.text import Localization
from squid_ui_discord.delivery import Messageable, Replyable
from squid_ui_discord.message_payload import MessagePayload

type InteractionSource[ClientT: discord.Client = discord.Client] = discord.Interaction[ClientT]
type CommandSource[BotT: commands.Bot = commands.Bot] = commands.Context[BotT]
type RequestSource = InteractionSource | CommandSource | discord.Message
type ResponseSource = RequestSource
type LocalizationSource = ResponseSource | Replyable

type DocumentContent = (
    Document[ComponentsV2Target] | LayoutNode[ComponentsV2Target] | Sequence[LayoutNode[ComponentsV2Target]]
)
type StaticContent = MessagePayload | str | discord.Embed | discord.ui.View | DocumentContent
type FacadeContent = StaticContent | Component[ComponentsV2Target]
# Both halves for the same reason `send_to` has two overloads: discord.py's overloaded `send`
# does not structurally satisfy the protocol, so a real channel needs naming on its own.
type SendDestination = discord.abc.Messageable | Messageable
type ResolvableRuntimeSource = discord.Client | RequestSource | Replyable
type LocalizationResolver = Callable[[LocalizationSource], Awaitable[Localization]]


__all__ = [
    "CommandSource",
    "DocumentContent",
    "FacadeContent",
    "InteractionSource",
    "LocalizationResolver",
    "LocalizationSource",
    "RequestSource",
    "ResolvableRuntimeSource",
    "ResponseSource",
    "SendDestination",
    "StaticContent",
]

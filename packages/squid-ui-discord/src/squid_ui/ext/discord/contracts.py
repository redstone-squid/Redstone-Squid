"""Static contracts shared by the owner-scoped Discord facade."""

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from squid_ui.ext.discord.request import Request

import discord
from discord.ext import commands
from squid_ui.authoring.document import Document
from squid_ui.authoring.semantic import LayoutNode
from squid_ui.content.text import Localization
from squid_ui.ext.discord.delivery import Messageable, Replyable
from squid_ui.ext.discord.messages.payload import MessagePayload
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.runtime.definition import Component

type RequestSource = discord.Interaction[Any] | commands.Context[Any] | discord.Message | Replyable

type DocumentContent[ClientT: discord.Client = discord.Client] = (
    Document[ComponentsV2Target, Request[Any, ClientT]]
    | LayoutNode[ComponentsV2Target, Request[Any, ClientT]]
    | Sequence[LayoutNode[ComponentsV2Target, Request[Any, ClientT]]]
)
type StaticContent[ClientT: discord.Client = discord.Client] = (
    MessagePayload | str | discord.Embed | discord.ui.View | DocumentContent[ClientT]
)
type FacadeContent[ClientT: discord.Client = discord.Client] = (
    StaticContent[ClientT] | Component[ComponentsV2Target, Request[Any, ClientT]]
)
# Both halves for the same reason `send_to` has two overloads: discord.py's overloaded `send`
# does not structurally satisfy the protocol, so a real channel needs naming on its own.
type SendDestination = discord.abc.Messageable | Messageable
type LocalizationResolver = Callable[[RequestSource], Awaitable[Localization]]


__all__ = [
    "DocumentContent",
    "FacadeContent",
    "LocalizationResolver",
    "RequestSource",
    "SendDestination",
    "StaticContent",
]

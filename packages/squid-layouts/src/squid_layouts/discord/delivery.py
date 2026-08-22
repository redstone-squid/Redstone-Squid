"""Send/edit mechanics for layout views.

A mount does not hold a message, it holds an :class:`EditHandle` — a way to write to one
already-sent message, and how long it is good for. Discord issues two kinds: the bot's own
credentials, which never expire, and an interaction's, which do. Everything that knows about
webhook tokens, `@original` semantics and HTTP error codes lives here, so callers only ever
ask whether a handle is expired and catch :class:`StaleHandleError` when it turns out to be.

The initial send is the mirror image: a mount holds no message yet, so it asks for one
through a :class:`Destination`. The destination returns a :class:`DeliveryReceipt` naming
both the observable message and the exact edit authority the operation created. It owns every
discord.py kwarg; the mount owns the stage/deliver/commit sequence around it.

Absorbs the three helpers that previously lived in the host bot (`edit_layout`,
`edit_interaction_layout`, `reply_layout`): every path defaults to `AllowedMentions.none()`
and clears legacy content/embed fields when converting a pre-Components-V2 message. Delivery
*policy* (ephemeral rules, DM fallback) stays host-side.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import discord

# Discord's way of saying the credentials behind a handle are gone.
_STALE_CODES = frozenset({10015, 10062, 50027})

# The response types that leave an interaction still pointed at the message it came from.
_UPDATE_RESPONSES = frozenset(
    {
        discord.InteractionResponseType.message_update,
        discord.InteractionResponseType.deferred_message_update,
    }
)


class StaleHandleError(Exception):
    """This handle no longer addresses its message.

    Discord expires the credentials behind an interaction, and a response spent on a new
    message stops addressing the old one. Raised in place of the underlying HTTP failure so
    callers never read status codes.
    """


class DeliveryAbandoned(Exception):
    """A destination chose not to deliver, and has already told the user why.

    Distinct from a failure: nothing reached Discord, but nothing went wrong either. The
    closed-DM path is the case — a payload too private for a channel, and no private channel
    to put it in. `Mount.send` discards the staged render rather than committing one nobody
    will ever see.
    """


class EditHandle(Protocol):
    """A way to write to one already-sent message, and how long it is good for."""

    permanent: bool
    """The bot's own credentials, which Discord does not expire."""

    expires_at: datetime | None
    """The deadline Discord stated, when it stated one. `None` is not a promise of permanence
    — borrowed credentials can go stale with no deadline we can read."""

    def expired(self) -> bool:
        """Whether the stated deadline, if there is one, has passed."""
        ...

    async def write(
        self,
        view: discord.ui.LayoutView,
        *,
        attachments: Sequence[discord.File | discord.Attachment] | None = None,
    ) -> None:
        """Show `view` on the message.

        Raises:
            StaleHandleError: This handle no longer addresses its message.
        """
        ...


def no_mentions() -> discord.AllowedMentions:
    """The default mention policy for rendered component text."""
    return discord.AllowedMentions.none()


def _uses_components_v2(message: discord.Message | None) -> bool:
    return bool(getattr(getattr(message, "flags", None), "components_v2", False))


def _is_stale(error: discord.HTTPException) -> bool:
    return error.code in _STALE_CODES


def _addresses_source(interaction: discord.Interaction[Any]) -> bool:
    """Whether the interaction's response left it still pointed at its own message.

    `send_message` moves the original response onto the new reply and `send_modal` leaves no
    message at all; only update-shaped responses leave it where it was.
    """
    response_type = interaction.response.type
    return response_type is None or response_type in _UPDATE_RESPONSES


def _legacy_fields(message: discord.Message | None) -> dict[str, Any]:
    """The clears a pre-Components-V2 message needs before it can show a layout."""
    return {} if _uses_components_v2(message) else {"content": None, "embed": None}


class _ChannelMessageHandle:
    """Writes to a channel message with the bot token."""

    def __init__(self, message: discord.Message) -> None:
        self._message = message
        self.permanent = True
        self.expires_at: datetime | None = None

    def expired(self) -> bool:
        return False

    async def write(
        self,
        view: discord.ui.LayoutView,
        *,
        attachments: Sequence[discord.File | discord.Attachment] | None = None,
    ) -> None:
        extra: dict[str, Any] = {} if attachments is None else {"attachments": list(attachments)}
        extra |= _legacy_fields(self._message)
        try:
            self._message = await self._message.edit(view=view, allowed_mentions=None, **extra)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the bot no longer has authority to edit this channel message"
                raise StaleHandleError(message) from error
            raise


class _WebhookMessageHandle:
    """Writes to one interaction webhook message by id."""

    def __init__(
        self,
        interaction: discord.Interaction[Any],
        message_id: int,
        message: discord.Message | None = None,
    ) -> None:
        self._interaction = interaction
        self._message_id = message_id
        self._message = message
        self.permanent = False
        self.expires_at: datetime | None = interaction.expires_at

    def expired(self) -> bool:
        return self._interaction.is_expired()

    async def write(
        self,
        view: discord.ui.LayoutView,
        *,
        attachments: Sequence[discord.File | discord.Attachment] | None = None,
    ) -> None:
        interaction = self._interaction
        extra: dict[str, Any] = {} if attachments is None else {"attachments": list(attachments)}
        extra |= _legacy_fields(self._message)
        try:
            # An unspent response is the cheap path: it edits the source message with no
            # extra round trip. Afterwards the message has to be named explicitly.
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view, **extra)
                return
            self._message = await interaction.followup.edit_message(self._message_id, view=view, **extra)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the credentials behind this interaction have expired"
                raise StaleHandleError(message) from error
            raise


class _OriginalResponseHandle:
    """Writes to an interaction's original response through the `@original` endpoint."""

    def __init__(
        self,
        interaction: discord.Interaction[Any],
        message: discord.Message | None = None,
    ) -> None:
        self._interaction = interaction
        self._message = message
        self.permanent = False
        self.expires_at: datetime | None = interaction.expires_at

    def expired(self) -> bool:
        return self._interaction.is_expired()

    async def write(
        self,
        view: discord.ui.LayoutView,
        *,
        attachments: Sequence[discord.File | discord.Attachment] | None = None,
    ) -> None:
        extra: dict[str, Any] = {} if attachments is None else {"attachments": list(attachments)}
        extra |= _legacy_fields(self._message)
        try:
            self._message = await self._interaction.edit_original_response(view=view, **extra)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the credentials behind this interaction have expired"
                raise StaleHandleError(message) from error
            raise


def handle_for(message: discord.Message) -> EditHandle:
    """A permanent bot-token handle for a message sent through a channel endpoint."""
    return _ChannelMessageHandle(message)


def handle_from(interaction: discord.Interaction[Any]) -> EditHandle | None:
    """A handle to the message `interaction` came from.

    `None` when the interaction has no message, or when its response has already been spent
    on something that is not that message — either way there is nothing here to write with.
    """
    message = interaction.message
    if message is None or not _addresses_source(interaction):
        return None
    return _WebhookMessageHandle(interaction, message.id, message)


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """What a delivery exposed and the authority it created to edit it."""

    message: discord.Message | None
    handle: EditHandle | None


async def respond_text(interaction: discord.Interaction[Any], content: str, *, ephemeral: bool = True) -> None:
    """Minimal text answer used for framework chrome (e.g. author-lock rejections)."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(content))
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=ephemeral, allowed_mentions=no_mentions())
        return
    await interaction.response.send_message(  # pyrefly: ignore[no-matching-overload]
        view=view, ephemeral=ephemeral, allowed_mentions=no_mentions()
    )


class Replyable(Protocol):
    """Whatever a command arrived on — `discord.ext.commands.Context`, structurally.

    Typed by shape so this package keeps out of the commands extension, and so a test double
    does not have to subclass one. `reply_to` additionally peeks at an `interaction`
    attribute to name the edit authority the send created; when a double carries one, it
    must answer `is_expired()`, `expires_at`, and `response.is_done()` like the real thing.
    """

    async def send(
        self,
        *,
        view: discord.ui.LayoutView,
        files: Sequence[discord.File],
        ephemeral: bool,
        allowed_mentions: discord.AllowedMentions,
    ) -> discord.Message: ...


class Destination(Protocol):
    """A way to create the message a mount will live on.

    The return value says what the mount gets to keep, not whether it worked:

    - a receipt with a handle — delivered, and here is the exact authority to edit it;
    - a receipt without a handle — delivered, but the operation exposed no edit authority;
    - raise :class:`DeliveryAbandoned` — nothing was delivered, deliberately, and the user
      already knows;
    - raise anything else — the delivery failed. The mount stays on its previous generation
      and the exception reaches the caller.
    """

    async def __call__(self, view: discord.ui.LayoutView, files: list[discord.File], /) -> DeliveryReceipt: ...


def reply_to(
    ctx: Replyable,
    *,
    ephemeral: bool = False,
    files: Sequence[discord.File] = (),
) -> Destination:
    """Answer the command that asked, in whatever channel it asked from."""

    async def send(view: discord.ui.LayoutView, rendered: list[discord.File]) -> DeliveryReceipt:
        interaction = getattr(ctx, "interaction", None)
        active = interaction is not None and not interaction.is_expired()
        response_done = active and interaction.response.is_done()
        message = await ctx.send(
            view=view,
            files=[*files, *rendered],
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
        )
        if not active:
            return DeliveryReceipt(message, handle_for(message))
        if response_done:
            return DeliveryReceipt(message, _WebhookMessageHandle(interaction, message.id, message))
        return DeliveryReceipt(message, _OriginalResponseHandle(interaction, message))

    return send


def respond_to(interaction: discord.Interaction[Any], *, ephemeral: bool = True, wait: bool = False) -> Destination:
    """Answer an interaction, whether or not it was already responded to.

    `wait` costs a round trip only when the caller needs the message itself. A fresh response
    remains writable through `@original` without fetching it.
    """

    async def send(view: discord.ui.LayoutView, files: list[discord.File]) -> DeliveryReceipt:
        if interaction.response.is_done():
            message = await interaction.followup.send(
                view=view, files=files, ephemeral=ephemeral, wait=wait, allowed_mentions=no_mentions()
            )
            if message is None:
                return DeliveryReceipt(None, None)
            return DeliveryReceipt(message, _WebhookMessageHandle(interaction, message.id, message))
        await interaction.response.send_message(  # pyrefly: ignore[no-matching-overload]
            view=view, files=files, ephemeral=ephemeral, allowed_mentions=no_mentions()
        )
        message = await interaction.original_response() if wait else None
        return DeliveryReceipt(message, _OriginalResponseHandle(interaction, message))

    return send

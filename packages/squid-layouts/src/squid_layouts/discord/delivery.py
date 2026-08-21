"""Send/edit mechanics for layout views.

A mount does not hold a message, it holds an :class:`EditHandle` — a way to write to one
already-sent message, and how long it is good for. Discord issues two kinds: the bot's own
credentials, which never expire, and an interaction's, which do. Everything that knows about
webhook tokens, `@original` semantics and HTTP error codes lives here, so callers only ever
ask whether a handle is expired and catch :class:`StaleHandleError` when it turns out to be.

The initial send is the mirror image: a mount holds no message yet, so it asks for one
through a :class:`Destination` — `async (view, files) -> Message | None`. The destination
owns every discord.py kwarg; the mount owns the stage/deliver/commit sequence around it.

Absorbs the three helpers that previously lived in the host bot (`edit_layout`,
`edit_interaction_layout`, `reply_layout`): every path defaults to `AllowedMentions.none()`
and clears legacy content/embed fields when converting a pre-Components-V2 message. Delivery
*policy* (ephemeral rules, DM fallback) stays host-side.
"""

from collections.abc import Sequence
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


def _is_ephemeral(message: discord.Message | None) -> bool:
    return bool(getattr(getattr(message, "flags", None), "ephemeral", False))


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


class _MessageHandle:
    """Writes through the message itself, with whatever credentials sent it."""

    def __init__(self, message: discord.Message) -> None:
        self._message = message
        # An ephemeral message lives only inside the interaction that produced it, so the
        # credentials its handle carries expire with that interaction. Discord states no
        # deadline for them, so `expired` cannot see it coming and `write` reports it instead.
        self.permanent = not _is_ephemeral(message)
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
                message = "the credentials this message was sent with have expired"
                raise StaleHandleError(message) from error
            raise


class _InteractionHandle:
    """Writes through the credentials one interaction carries."""

    def __init__(self, interaction: discord.Interaction[Any], message_id: int) -> None:
        self._interaction = interaction
        self._message_id = message_id
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
        extra |= _legacy_fields(interaction.message)
        try:
            # An unspent response is the cheap path: it edits the source message with no
            # extra round trip. Afterwards the message has to be named explicitly.
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view, **extra)
                return
            await interaction.followup.edit_message(self._message_id, view=view, **extra)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the credentials behind this interaction have expired"
                raise StaleHandleError(message) from error
            raise


def handle_for(message: discord.Message) -> EditHandle:
    """A handle that writes to `message` with the credentials that sent it."""
    return _MessageHandle(message)


def handle_from(interaction: discord.Interaction[Any]) -> EditHandle | None:
    """A handle to the message `interaction` came from.

    `None` when the interaction has no message, or when its response has already been spent
    on something that is not that message — either way there is nothing here to write with.
    """
    message = interaction.message
    if message is None or not _addresses_source(interaction):
        return None
    return _InteractionHandle(interaction, message.id)


async def respond(
    interaction: discord.Interaction[Any],
    view: discord.ui.LayoutView,
    *,
    ephemeral: bool = True,
    wait: bool = False,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message | None:
    """Answer an interaction with a view, whether or not it was already responded to.

    `wait` costs a round trip to fetch the message back, so it is opt-in and only worth it
    for a view that needs to edit itself later.
    """
    mentions = allowed_mentions if allowed_mentions is not None else no_mentions()
    if interaction.response.is_done():
        message = await interaction.followup.send(view=view, ephemeral=ephemeral, wait=wait, allowed_mentions=mentions)
        return message if wait else None
    await interaction.response.send_message(  # pyrefly: ignore[no-matching-overload]
        view=view, ephemeral=ephemeral, allowed_mentions=mentions
    )
    return await interaction.original_response() if wait else None


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
    does not have to subclass one.
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

    - a `Message` — delivered, and here are the credentials to edit it later;
    - `None` — delivered, but no handle came back (an unwaited interaction response). The
      mount commits and mints its handle from the first click instead;
    - raise :class:`DeliveryAbandoned` — nothing was delivered, deliberately, and the user
      already knows;
    - raise anything else — the delivery failed. The mount stays on its previous generation
      and the exception reaches the caller.
    """

    async def __call__(self, view: discord.ui.LayoutView, files: list[discord.File]) -> discord.Message | None: ...


def reply_to(ctx: Replyable, *, ephemeral: bool = False) -> Destination:
    """Answer the command that asked, in whatever channel it asked from."""

    async def send(view: discord.ui.LayoutView, files: list[discord.File]) -> discord.Message | None:
        return await ctx.send(view=view, files=files, ephemeral=ephemeral, allowed_mentions=no_mentions())

    return send


def respond_to(interaction: discord.Interaction[Any], *, ephemeral: bool = True, wait: bool = False) -> Destination:
    """Answer an interaction, whether or not it was already responded to.

    `wait` costs a round trip to fetch the message back, so it is opt-in: pass it when the
    caller needs the message itself, or when the mount must still be writable with nobody
    having clicked it. Otherwise the mount runs handle-less until the first click renews it.
    """

    async def send(view: discord.ui.LayoutView, files: list[discord.File]) -> discord.Message | None:
        if interaction.response.is_done():
            message = await interaction.followup.send(
                view=view, files=files, ephemeral=ephemeral, wait=wait, allowed_mentions=no_mentions()
            )
            return message if wait else None
        await interaction.response.send_message(  # pyrefly: ignore[no-matching-overload]
            view=view, files=files, ephemeral=ephemeral, allowed_mentions=no_mentions()
        )
        return await interaction.original_response() if wait else None

    return send

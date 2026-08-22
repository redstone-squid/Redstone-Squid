"""Send/edit mechanics for rendered Discord messages.

A mount does not hold a message, it holds an :class:`EditHandle` — a way to write to one
already-sent message, and how long it is good for. Discord issues two kinds: the bot's own
credentials, which never expire, and an interaction's, which do. Everything that knows about
webhook tokens, `@original` semantics and HTTP error codes lives here, so callers only ever
ask whether a handle is expired and catch :class:`StaleHandleError` when it turns out to be.

The initial send is the mirror image: a mount holds no message yet, so it asks for one
through a :class:`Destination`. The destination returns a :class:`DeliveryReceipt` naming
both the observable message and the exact edit authority the operation created. It owns every
discord.py kwarg; the mount owns the stage/deliver/commit sequence around it.

Both halves move :class:`~squid_layouts.discord.presentation.DiscordPresentation` values,
not views: a handle knows which mode the message it addresses is in, so the legacy fields a
pre-Components-V2 message has to clear are a stated transition rather than a guess at a
`discord.Message` that is very often `None`.

Absorbs the three helpers that previously lived in the host bot (`edit_layout`,
`edit_interaction_layout`, `reply_layout`): every path defaults to `AllowedMentions.none()`.
Delivery *policy* (ephemeral rules, DM fallback) stays host-side.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import discord

from squid_layouts.discord.presentation import DiscordMode, DiscordPresentation, mode_of
from squid_layouts.errors import LayoutError, LimitViolationError
from squid_layouts.planning.limits import LIMITS

# Discord's way of saying the credentials behind a handle are gone.
_STALE_CODES = frozenset({10015, 10062, 50027})

# The response types that leave an interaction still pointed at the message it came from.
_UPDATE_RESPONSES = frozenset(
    {
        discord.InteractionResponseType.message_update,
        discord.InteractionResponseType.deferred_message_update,
    }
)


class StaleHandleError(LayoutError):
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


@dataclass(frozen=True, slots=True)
class Delivered:
    """A mount render committed through a destination."""

    receipt: DeliveryReceipt


@dataclass(frozen=True, slots=True)
class Abandoned:
    """A destination deliberately declined to deliver a mount."""


type SendResult = Delivered | Abandoned


class EditHandle(Protocol):
    """A way to write to one already-sent message, and how long it is good for."""

    permanent: bool
    """The bot's own credentials, which Discord does not expire."""

    expires_at: datetime | None
    """The deadline Discord stated, when it stated one. `None` is not a promise of permanence
    — borrowed credentials can go stale with no deadline we can read."""

    mode: DiscordMode
    """Which component mode the message is in *now*, which is what says whether the next
    write is a transition Discord offers. Kept current by every successful write."""

    def expired(self) -> bool:
        """Whether the stated deadline, if there is one, has passed."""
        ...

    async def write(self, presentation: DiscordPresentation, *, keep_attachments: bool = False) -> None:
        """Replace what the message shows with `presentation`.

        `keep_attachments` leaves the message's files alone; without it the presentation's
        own assets become the message's whole attachment set.

        Raises:
            StaleHandleError: This handle no longer addresses its message.
            DiscordModeError: The message cannot move to `presentation.mode`.
        """
        ...


def no_mentions() -> discord.AllowedMentions:
    """The default mention policy for rendered component text."""
    return discord.AllowedMentions.none()


def _is_stale(error: discord.HTTPException) -> bool:
    return error.code in _STALE_CODES


def _addresses_source(interaction: discord.Interaction[Any]) -> bool:
    """Whether the interaction's response left it still pointed at its own message.

    `send_message` moves the original response onto the new reply and `send_modal` leaves no
    message at all; only update-shaped responses leave it where it was.
    """
    response_type = interaction.response.type
    return response_type is None or response_type in _UPDATE_RESPONSES


def _write_fields(
    presentation: DiscordPresentation, previous: DiscordMode, *, keep_attachments: bool
) -> dict[str, Any]:
    """The edit kwargs for one write, transition matrix included."""
    fields = presentation._edit_fields(previous)
    if not keep_attachments:
        fields["attachments"] = presentation.files()
    return fields


def _merged_files(host: Sequence[discord.File], presentation: DiscordPresentation) -> list[discord.File]:
    """The host's own files followed by the presentation's, refused if Discord cannot take them.

    Overflow is rejected here rather than by Discord, whose answer names neither the count
    nor which half of the list overran it.
    """
    files = [*host, *presentation.files()]
    if len(files) > LIMITS.attachments:
        message = f"a Discord message carries at most {LIMITS.attachments} attachments, not {len(files)}"
        raise LimitViolationError([message])
    return files


class _ChannelMessageHandle:
    """Writes to a channel message with the bot token."""

    def __init__(self, message: discord.Message, *, mode: DiscordMode | None = None) -> None:
        self._message = message
        self.permanent = True
        self.expires_at: datetime | None = None
        self.mode = mode if mode is not None else mode_of(message)

    def expired(self) -> bool:
        return False

    async def write(self, presentation: DiscordPresentation, *, keep_attachments: bool = False) -> None:
        fields = _write_fields(presentation, self.mode, keep_attachments=keep_attachments)
        try:
            self._message = await self._message.edit(allowed_mentions=None, **fields)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the bot no longer has authority to edit this channel message"
                raise StaleHandleError(message) from error
            raise
        self.mode = presentation.mode


class _WebhookMessageHandle:
    """Writes to one interaction webhook message by id."""

    def __init__(
        self,
        interaction: discord.Interaction[Any],
        message_id: int,
        message: discord.Message | None = None,
        *,
        mode: DiscordMode,
    ) -> None:
        self._interaction = interaction
        self._message_id = message_id
        self._message = message
        self.permanent = False
        self.expires_at: datetime | None = interaction.expires_at
        self.mode = mode

    def expired(self) -> bool:
        return self._interaction.is_expired()

    async def write(self, presentation: DiscordPresentation, *, keep_attachments: bool = False) -> None:
        interaction = self._interaction
        fields = _write_fields(presentation, self.mode, keep_attachments=keep_attachments)
        try:
            # An unspent response is the cheap path: it edits the source message with no
            # extra round trip. Afterwards the message has to be named explicitly.
            if not interaction.response.is_done():
                await interaction.response.edit_message(**fields)
            else:
                self._message = await interaction.followup.edit_message(self._message_id, **fields)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the credentials behind this interaction have expired"
                raise StaleHandleError(message) from error
            raise
        self.mode = presentation.mode


class _OriginalResponseHandle:
    """Writes to an interaction's original response through the `@original` endpoint."""

    def __init__(
        self,
        interaction: discord.Interaction[Any],
        message: discord.Message | None = None,
        *,
        mode: DiscordMode,
    ) -> None:
        self._interaction = interaction
        self._message = message
        self.permanent = False
        self.expires_at: datetime | None = interaction.expires_at
        self.mode = mode

    def expired(self) -> bool:
        return self._interaction.is_expired()

    async def write(self, presentation: DiscordPresentation, *, keep_attachments: bool = False) -> None:
        fields = _write_fields(presentation, self.mode, keep_attachments=keep_attachments)
        try:
            self._message = await self._interaction.edit_original_response(**fields)
        except discord.HTTPException as error:
            if _is_stale(error):
                message = "the credentials behind this interaction have expired"
                raise StaleHandleError(message) from error
            raise
        self.mode = presentation.mode


def handle_for(message: discord.Message, *, mode: DiscordMode | None = None) -> EditHandle:
    """A permanent bot-token handle for a message sent through a channel endpoint.

    `mode` states what the message is showing when the caller knows better than the flag —
    a just-delivered presentation, or a mode read back out of a durable record.
    """
    return _ChannelMessageHandle(message, mode=mode)


def handle_from(interaction: discord.Interaction[Any]) -> EditHandle | None:
    """A handle to the message `interaction` came from.

    `None` when the interaction has no message, or when its response has already been spent
    on something that is not that message — either way there is nothing here to write with.
    """
    message = interaction.message
    if message is None or not _addresses_source(interaction):
        return None
    return _WebhookMessageHandle(interaction, message.id, message, mode=mode_of(message))


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """What a delivery exposed and the authority it created to edit it."""

    message: discord.Message | None
    handle: EditHandle | None
    message_id: int | None = None
    ephemeral: bool | None = None

    def __post_init__(self) -> None:
        """Fill metadata available on ordinary message-returning delivery paths."""
        if self.message is None:
            return
        if self.message_id is None:
            object.__setattr__(self, "message_id", self.message.id)
        if self.ephemeral is None:
            flags = getattr(self.message, "flags", None)
            ephemeral = getattr(flags, "ephemeral", None)
            if isinstance(ephemeral, bool):
                object.__setattr__(self, "ephemeral", ephemeral)


def _callback_receipt(
    response: discord.InteractionCallbackResponse[Any],
    handle: EditHandle,
    *,
    fallback: discord.Message | None = None,
) -> DeliveryReceipt:
    """Keep the message metadata Discord returned with an interaction callback."""
    resource = response.resource
    message = resource if isinstance(resource, discord.Message) else fallback
    return DeliveryReceipt(message, handle, response.message_id, response.is_ephemeral())


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

    `content`, `embeds` and `view` are whatever the presentation names: a Components V2 send
    passes only `view`, because naming the legacy fields beside the V2 flag is a payload
    Discord is entitled to reject.

    `view` is `Any` because no single signature covers both modes — discord.py's own `send`
    is overloaded precisely so that a `LayoutView` and a `content` cannot be named together.
    :class:`DiscordPresentation` is what enforces that, at construction, for both of them.
    """

    async def send(
        self,
        *,
        files: Sequence[discord.File],
        ephemeral: bool,
        allowed_mentions: discord.AllowedMentions,
        content: str | None = ...,
        embeds: Sequence[discord.Embed] = ...,
        view: Any = ...,
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

    The presentation is the whole payload Squid owns. Transport policy stays here:
    ephemerality, waiting, allowed mentions, DM fallback, and the host's own files, which
    are merged ahead of `presentation.files()`.
    """

    async def __call__(self, presentation: DiscordPresentation, /) -> DeliveryReceipt: ...


def reply_to(
    ctx: Replyable,
    *,
    ephemeral: bool = False,
    files: Sequence[discord.File] = (),
) -> Destination:
    """Answer the command that asked, in whatever channel it asked from."""

    async def send(presentation: DiscordPresentation) -> DeliveryReceipt:
        interaction = getattr(ctx, "interaction", None)
        active = interaction is not None and not interaction.is_expired()
        response_done = active and interaction.response.is_done()
        message = await ctx.send(
            files=_merged_files(files, presentation),
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
            **presentation._send_fields(),
        )
        mode = presentation.mode
        if not active:
            return DeliveryReceipt(message, handle_for(message, mode=mode))
        if response_done:
            return DeliveryReceipt(message, _WebhookMessageHandle(interaction, message.id, message, mode=mode))
        return DeliveryReceipt(message, _OriginalResponseHandle(interaction, message, mode=mode))

    return send


def respond_to(interaction: discord.Interaction[Any], *, ephemeral: bool = True, wait: bool = False) -> Destination:
    """Answer an interaction, whether or not it was already responded to.

    `wait` costs a round trip only when the caller needs the message itself. A fresh response
    remains writable through `@original` without fetching it.
    """

    async def send(presentation: DiscordPresentation) -> DeliveryReceipt:
        files = _merged_files((), presentation)
        mode = presentation.mode
        if interaction.response.is_done():
            message = await interaction.followup.send(
                files=files,
                ephemeral=ephemeral,
                wait=wait,
                allowed_mentions=no_mentions(),
                **presentation._send_fields(),
            )
            if message is None:
                return DeliveryReceipt(None, None)
            return DeliveryReceipt(message, _WebhookMessageHandle(interaction, message.id, message, mode=mode))
        response = await interaction.response.send_message(  # pyrefly: ignore[no-matching-overload]
            files=files, ephemeral=ephemeral, allowed_mentions=no_mentions(), **presentation._send_fields()
        )
        callback_message = response.resource if isinstance(response.resource, discord.Message) else None
        message = await interaction.original_response() if wait and callback_message is None else callback_message
        handle = _OriginalResponseHandle(interaction, message, mode=mode)
        return _callback_receipt(response, handle, fallback=message)

    return send

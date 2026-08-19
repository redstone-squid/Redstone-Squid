"""Send/edit mechanics for layout views.

Absorbs the three helpers that previously lived in the host bot (`edit_layout`,
`edit_interaction_layout`, `reply_layout`): every path defaults to
`AllowedMentions.none()` and clears legacy content/embed fields when converting a
pre-Components-V2 message. Delivery *policy* (ephemeral rules, DM fallback) stays host-side.
"""

from collections.abc import Sequence
from typing import Any

import discord


def no_mentions() -> discord.AllowedMentions:
    """The default mention policy for rendered component text."""
    return discord.AllowedMentions.none()


def _uses_components_v2(message: discord.Message | None) -> bool:
    return bool(getattr(getattr(message, "flags", None), "components_v2", False))


async def apply(
    message: discord.Message,
    view: discord.ui.LayoutView,
    *,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message:
    """Edit a message to show `view`, clearing legacy fields on first V2 conversion."""
    if not _uses_components_v2(message):
        return await message.edit(content=None, embed=None, view=view, allowed_mentions=allowed_mentions)
    return await message.edit(view=view, allowed_mentions=allowed_mentions)


async def apply_interaction(
    interaction: discord.Interaction[Any],
    view: discord.ui.LayoutView,
    *,
    attachments: Sequence[discord.File | discord.Attachment] | None = None,
) -> None:
    """Edit the interaction's source message, converting legacy payloads when needed.

    `attachments` replaces the message's files, so `[]` strips them and omitting the argument
    leaves them alone. When the callback has already responded, the edit goes through the
    original response instead.
    """
    extra: dict[str, Any] = {} if attachments is None else {"attachments": list(attachments)}
    if interaction.message is not None and not _uses_components_v2(interaction.message):
        extra |= {"content": None, "embed": None}
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view, **extra)
        return
    await interaction.response.edit_message(view=view, **extra)


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

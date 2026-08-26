"""Who sees a reply.

Three patterns used to coexist with no rule behind them (audit C2): always ephemeral,
`ephemeral=ctx.interaction is not None`, and always public — so near-identical commands
disagreed, `records rebuild` answering privately while `records lookup` answered in the
channel. The rule is:

1. **A decision about shared content answers publicly.** Approving a build, crediting a
   creator name, archiving a message: the reply is the record others need, so it goes where
   they can read it.
2. **A read of shared content answers publicly.** Anyone could have run it, and it is usually
   run to show somebody. In practice this is the ungated half of the command surface.
3. **Anything about the person who asked, and any staff working material, answers
   privately.** Your account, your subscriptions, a review queue, a diagnostic, a settings
   panel: nobody else in the channel is better off seeing it.
4. **Errors and refusals answer privately**, which the shared error presenter already does.

`Context.send` drops `ephemeral` when there is no interaction — it forwards to
`Messageable.send`, which has no such parameter — so "privately" is a best effort on the
prefix side and `personal(ctx)` says so by spelling out the condition. Where the payload must
never reach a channel at all, `deliver_privately` uses direct messages instead; that is the
difference between a reply that is merely nobody else's business and one that is a credential
or a traceback.
"""

from collections.abc import Sequence
from typing import Any

import discord
from discord.ext.commands import Context

import squid_discord as sd
from squid.bot.i18n import t
from squid.bot.ui import error_layout, info_layout, reply_presentation
from squid.core.i18n import _
from squid_discord import send_to


def personal(ctx: Context[Any]) -> bool:
    """`ephemeral=` for a reply nobody but the caller needs.

    True on the slash path, where Discord can keep a reply to one reader, and False on the
    prefix path, where it cannot — the parameter would be dropped either way, so the condition
    is written out rather than implied by a literal `True`.
    """
    return ctx.interaction is not None


async def deliver_privately(
    ctx: Context[Any],
    presentation: sd.DiscordPresentation,
    *,
    reason: str,
    locale: str | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
    files: Sequence[discord.File] = (),
) -> discord.Message | None:
    """Answer where only the caller can read it, whatever the transport invoked us.

    For payloads a channel must never hold: a traceback naming internal paths, a code that
    hands an account over. Ephemeral on the slash side, direct messages on the prefix side,
    with a line in the channel saying so. `reason` is that line — it says what this particular
    payload is, since "the bot DMed you" without a why reads as a malfunction.

    A closed DM delivers nothing and says so. It deliberately does not fall back to the
    channel, because the channel is exactly what the payload must not reach.
    """
    if ctx.interaction is not None or ctx.guild is None:
        result = await sd.reply_to(
            ctx,
            ephemeral=True,
            files=files,
            allowed_mentions=allowed_mentions,
        )(presentation)
        return result.message

    try:
        result = await send_to(ctx.author, files=files, allowed_mentions=allowed_mentions)(presentation)
    except discord.Forbidden:
        await reply_presentation(
            ctx,
            error_layout(
                t(locale, _("Nowhere private to send this")),
                t(
                    locale,
                    _(
                        "This reply is too private for a channel, and your DMs from this server are "
                        "closed. Run the command in a direct message with me, or allow DMs and retry."
                    ),
                ),
            ),
        )
        return None
    await reply_presentation(
        ctx,
        info_layout(t(locale, _("Sent by direct message")), reason),
    )
    return result.message

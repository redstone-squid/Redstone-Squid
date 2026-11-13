"""Close and refresh controls attached to generic poll cards.

The message containing the clicked control identifies the poll. Both actions use
the session's close authorization and read an existing account without creating
one. Shared card labels are fixed; personal replies use the caller's locale.
"""

from typing import TYPE_CHECKING, Any, cast

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.routes._root import _feature_group
from squid.bot.ui import text_node
from squid.bot.voting.actors import describe_rejection, resolve_actor
from squid.core.actors import Actor
from squid.core.i18n import tr
from squid.voting.domain import VoteRejection, VoteSessionSnapshot
from squid_i18n import md_tr

if TYPE_CHECKING:
    import squid.bot.app
    from squid.bot.voting.vote import VoteCog


polls, _polls_created = _feature_group("polls")
poll_close = polls.define("close", aliases=("poll:close",))
poll_refresh = polls.define("refresh", aliases=("poll:refresh",))


def poll_controls() -> sl.semantic.ActionControls:
    """The control row an open poll's card ends with."""
    return sl.action_controls(
        sl.routed_action_control(
            tr(t"bot-voting-controls-close-poll = Close poll"), poll_close.id(), key="close", tone=sl.Tone.DANGER
        ),
        sl.routed_action_control(
            tr(t"bot-voting-controls-refresh-weights = Refresh weights"), poll_refresh.id(), key="refresh"
        ),
        key="poll.controls",
    )


@polls.route(poll_close)
async def close_poll(interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:
    """End a poll early, recounting its cards first and then tallying it where it stands."""
    request = await sd.request(interaction)
    authorized = await _authorize(request)
    if authorized is None:
        return
    snapshot, actor = authorized
    bot = interaction.client
    # Count reactions added while the bot was offline before the result is fixed. Reading
    # every voter can outlast Discord's three-second reply window, hence the deferral.
    vote_cog = cast("VoteCog | None", bot.get_cog("VoteCog"))
    if vote_cog is not None:
        await request.defer()
        await vote_cog.reactions.recount(snapshot.id)
    result = await bot.services.votes.close(snapshot.id, actor)
    if result.rejection is not None or result.session is None:
        await _refuse(request, result.rejection or VoteRejection.NOT_FOUND)
        return
    await bot.refresh_posts("vote_session", str(result.session.id))
    await request.respond(text_node(tr(t"bot-voting-controls-poll-closed = Poll closed.")), audience="personal")


@polls.route(poll_refresh)
async def refresh_poll(interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:
    """Recompute cached role weights, for a poll whose voters gained or lost roles."""
    request = await sd.request(interaction)
    authorized = await _authorize(request)
    if authorized is None:
        return
    snapshot, _actor = authorized
    bot = interaction.client
    result = await bot.services.votes.refresh(snapshot.id)
    if result.session is not None:
        await bot.refresh_posts("vote_session", str(result.session.id))
    text = tr(t"bot-voting-controls-poll-weights-refreshed = Poll weights refreshed.")
    if not result.complete:
        # Report unresolved voters as a count without exposing their account IDs.
        count = len(result.unresolved_account_ids)
        text += " " + md_tr(
            t"vote-unresolved-voter-cache-retained = {count} voter(s) could not be resolved, "
            t"so their cached weight was kept."
        )
    await request.respond(text_node(text), audience="personal")


async def _authorize(
    request: sd.Request[squid.bot.app.RedstoneSquid],
) -> tuple[VoteSessionSnapshot, Actor] | None:
    """The session and actor behind a click, or `None` once the click has been refused.

    Refreshing recomputes the weights a close would act on, so both controls are gated by
    the session's own `can_close` rather than by a second copy of the rule.
    """
    interaction = request.interaction
    assert interaction is not None
    bot = interaction.client
    message = interaction.message
    if message is None or not isinstance(interaction.user, discord.Member):
        await _refuse(request, VoteRejection.WRONG_GUILD)
        return None

    snapshot = await bot.services.votes.get_session_for_message(message.id)
    if snapshot is None:
        await _refuse(request, VoteRejection.NOT_FOUND)
        return None

    account_id = await bot.account_ids.resolve(bot.services.accounts, interaction.user.id)
    actor = await resolve_actor(bot, interaction.user, account_id=account_id or 0)
    rejection = snapshot.can_close(actor)
    if rejection is not None:
        await _refuse(request, rejection)
        return None
    return snapshot, actor


async def _refuse(request: sd.Request[Any], rejection: VoteRejection) -> None:
    await request.respond(
        text_node(describe_rejection(rejection)),
        audience="personal",
    )

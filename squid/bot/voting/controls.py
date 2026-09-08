"""The buttons a generic poll's own card carries.

The poll a click refers to is the message the button sits on; nothing is encoded in the custom id. Neither action
stores anything about the clicker, so neither asks for consent: the account id is read, never minted.

Labels are not translated, since one card is read by everyone in the channel; what a click replies is, because
that reply has one reader.
"""

from typing import TYPE_CHECKING, Any

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.routes._root import _feature_group, _feature_route
from squid.bot.ui import text_node
from squid.bot.voting.actors import describe_rejection, resolve_actor
from squid.core.i18n import tr
from squid.voting.domain import VoteActor, VoteRejection, VoteSessionSnapshot

if TYPE_CHECKING:
    import squid.bot.app


polls, _polls_created = _feature_group("polls")
poll_close = _feature_route(polls, "close", aliases=("poll:close",))
poll_refresh = _feature_route(polls, "refresh", aliases=("poll:refresh",))


def poll_controls() -> sl.semantic.ActionControls:
    """The control row an open poll's card ends with."""
    return sl.action_controls(
        sl.routed_action_control("Close poll", poll_close.id(), key="close", tone=sl.Tone.DANGER),
        sl.routed_action_control("Refresh weights", poll_refresh.id(), key="refresh"),
        key="poll.controls",
    )


@polls.route(poll_close)
async def close_poll(interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:
    """End a poll early, tallying it where it stands."""
    authorized = await _authorize(interaction)
    if authorized is None:
        return
    _snapshot, actor = authorized
    bot = interaction.client
    assert interaction.message is not None
    result = await bot.services.votes.close(interaction.message.id, actor)
    if result.rejection is not None or result.session is None:
        await _refuse(interaction, result.rejection or VoteRejection.NOT_FOUND)
        return
    await bot.refresh_posts("vote_session", str(result.session.id))
    await bot.app_ui.respond(interaction, text_node(tr("Poll closed.")), audience="personal")


@polls.route(poll_refresh)
async def refresh_poll(interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:
    """Recompute cached role weights, for a poll whose voters gained or lost roles."""
    authorized = await _authorize(interaction)
    if authorized is None:
        return
    _snapshot, _actor = authorized
    bot = interaction.client
    assert interaction.message is not None
    result = await bot.services.votes.refresh(interaction.message.id)
    if result.session is not None:
        await bot.refresh_posts("vote_session", str(result.session.id))
    text = tr("Poll weights refreshed.")
    if not result.complete:
        # A count only; account ids are not shown to users.
        text += " " + tr(
            "{count} voter(s) could not be resolved, so their cached weight was kept.",
            count=len(result.unresolved_account_ids),
        )
    await bot.app_ui.respond(interaction, text_node(text), audience="personal")


async def _authorize(
    interaction: discord.Interaction[squid.bot.app.RedstoneSquid],
) -> tuple[VoteSessionSnapshot, VoteActor] | None:
    """The session and actor behind a click, or None once the click has been refused.

    Both controls are gated by the session's `can_close`: refreshing recomputes the weights a close acts on.
    """
    bot = interaction.client
    message = interaction.message
    if message is None or not isinstance(interaction.user, discord.Member):
        await _refuse(interaction, VoteRejection.WRONG_GUILD)
        return None

    snapshot = await bot.services.votes.get_session(message.id)
    if snapshot is None:
        await _refuse(interaction, VoteRejection.NOT_FOUND)
        return None

    account_id = await bot.account_ids.resolve(bot.services.accounts, interaction.user.id)
    actor = await resolve_actor(bot, interaction.user, account_id=account_id or 0)
    rejection = snapshot.can_close(actor)
    if rejection is not None:
        await _refuse(interaction, rejection)
        return None
    return snapshot, actor


async def _refuse(interaction: discord.Interaction[Any], rejection: VoteRejection) -> None:
    runtime = sd.DiscordUIRuntime.of(interaction)
    await runtime.scope(runtime.client).respond(
        interaction,
        text_node(describe_rejection(rejection)),
        audience="personal",
    )

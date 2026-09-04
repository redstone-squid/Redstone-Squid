"""The buttons a generic poll's own card carries.

Closing and refreshing a poll used to be `/poll close` and `/poll refresh`, each taking a
`discord.Message` — which in slash form means pasting a link to the card you are already
looking at (audit C4). Both gestures belong on the card, so they live here as routed
controls, and the poll a click refers to is the message the button sits on: nothing is
encoded in the custom id, because nothing has to be.

Neither action stores anything about whoever clicked, so neither asks for consent. The
account id below is read, never minted; someone without one cannot be the poll's author,
and staff closing another person's poll should not gain an account row for it.

Labels are not translated. One card is read by everyone in the channel, so rendering it in
the guild's locale would still be the wrong language for most of them; what a click
*replies* is translated, because that reply has exactly one reader.
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
    await bot.app_ui.respond(interaction, text_node(tr(t"Poll closed.")), audience="personal")


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
    text = tr(tr(t"Poll weights refreshed."))
    if not result.complete:
        # A count, not the raw account ids the command used to print (audit C5).
        count = len(result.unresolved_account_ids)
        text += " " + tr(tr(t"{count} voter(s) could not be resolved, so their cached weight was kept."))
    await bot.app_ui.respond(interaction, text_node(text), audience="personal")


async def _authorize(
    interaction: discord.Interaction[squid.bot.app.RedstoneSquid],
) -> tuple[VoteSessionSnapshot, VoteActor] | None:
    """The session and actor behind a click, or `None` once the click has been refused.

    Refreshing recomputes the weights a close would act on, so both controls are gated by
    the session's own `can_close` rather than by a second copy of the rule.
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

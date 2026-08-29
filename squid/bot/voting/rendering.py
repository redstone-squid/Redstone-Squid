"""Vote card rendering, independent of where the cards live.

Kept apart from the session classes so a card can be produced from a snapshot alone.
Rendering used to be a method that also fetched and edited its own messages, which is
why every session had to track the messages it had sent.
"""

from textwrap import dedent

import discord

from squid.bot.utils.components import (
    DISCORD_GREEN,
    DISCORD_RED,
    DISCORD_YELLOW,
    CardField,
    StaticLayout,
    card_layout,
    text_layout,
)
from squid.voting.domain import VoteChoice, VoteSessionSnapshot


def primary_emoji(snapshot: VoteSessionSnapshot, choice: VoteChoice, guild_id: int | None = None) -> str:
    """Return the first configured emoji for a vote choice in one guild."""
    options = snapshot.options_for_guild(guild_id or 0)
    return next(option.emoji for option in options if option.choice is choice)


def render_build_review(
    container: discord.ui.Container[discord.ui.LayoutView],
    snapshot: VoteSessionSnapshot,
    guild_id: int | None,
) -> StaticLayout:
    """Compose a build card with the review vote state beneath it."""
    container.add_item(discord.ui.Separator())
    if snapshot.status == "closed":
        result_label = {
            "approved": "Approved",
            "denied": "Denied",
            "cancelled": "Closed without a decision",
            "pending": "Closed without a decision",
        }[snapshot.result]
        vote_text = f"### Vote closed — {result_label}\n**Final score:** {snapshot.net_votes:g}"
    else:
        approve = primary_emoji(snapshot, VoteChoice.APPROVE, guild_id)
        deny = primary_emoji(snapshot, VoteChoice.DENY, guild_id)
        # A build review always threshold-closes, so both thresholds are set; the
        # nullable ones belong to generic polls, which never reach this renderer.
        assert snapshot.pass_threshold is not None
        assert snapshot.fail_threshold is not None
        vote_text = (
            "### Vote in progress\n"
            f"React with {approve} to **accept** or {deny} to **deny**. Votes are anonymous.\n"
            f"**Accept:** {snapshot.upvotes:g}/{snapshot.pass_threshold}  •  "
            f"**Deny:** {snapshot.downvotes:g}/{-snapshot.fail_threshold}"
        )
    container.add_item(discord.ui.TextDisplay(vote_text))
    return StaticLayout(container)


def render_delete_log(snapshot: VoteSessionSnapshot, target_content: str) -> StaticLayout:
    """Render the card asking whether a logged message should be deleted."""
    match snapshot.result if snapshot.status == "closed" else "pending":
        case "pending":
            title = "Vote to Delete Log"
            action = (
                f"React with {primary_emoji(snapshot, VoteChoice.APPROVE)} to upvote or "
                f"{primary_emoji(snapshot, VoteChoice.DENY)} to downvote."
            )
            accent_colour = DISCORD_YELLOW
        case "approved":
            title = "Vote to Delete Log: Passed"
            action = ""
            accent_colour = DISCORD_GREEN
        case "denied":
            title = "Vote to Delete Log: Failed"
            action = ""
            accent_colour = DISCORD_RED
        case _:
            title = "Vote to Delete Log: Closed"
            action = ""
            accent_colour = DISCORD_YELLOW

    description = dedent(f"""
        {action}

        **Log content**
        {target_content}
        """).strip()
    return card_layout(
        title,
        description,
        accent_colour=accent_colour,
        fields=(
            CardField("Upvotes", str(snapshot.upvotes)),
            CardField("Downvotes", str(snapshot.downvotes)),
            CardField("Net votes", str(snapshot.net_votes)),
        ),
    )


def render_generic_poll(snapshot: VoteSessionSnapshot) -> StaticLayout:
    """Render a user-created poll, honouring its visibility setting."""
    return text_layout(generic_poll_text(snapshot))


def generic_poll_text(snapshot: VoteSessionSnapshot) -> str:
    """The body of a generic poll card."""
    poll = snapshot.poll
    assert poll is not None
    closed = snapshot.status == "closed"
    show_totals = poll.visibility != "anonymous_hidden" or closed
    raw = snapshot.raw_tallies()
    weighted = snapshot.weighted_tallies()
    lines = [f"## {poll.question}"]
    # A poll drafted outside a guild has no aliases of its own, so it falls back to
    # the unscoped options rather than to some arbitrary guild's palette.
    for option in snapshot.options_for_guild(poll.guild_id or 0):
        line = f"{option.emoji} **{option.label}**"
        if show_totals:
            line += (
                f" — {raw.get(option.identifier or '', 0)} votes, {weighted.get(option.identifier or '', 0):g} weighted"
            )
        if poll.visibility == "visible_live" and show_totals:
            voters = [f"<@{vote.discord_id}>" for vote in snapshot.selections if vote.option_id == option.identifier]
            if voters:
                line += f" ({', '.join(voters)})"
        lines.append(line)
    if closed:
        totals = snapshot.weighted_tallies()
        best = max(totals.values(), default=0)
        winners = [
            option.label or option.identifier or option.emoji
            for option in snapshot.options
            if totals.get(option.identifier or "", 0) == best
        ]
        outcome = (
            "No votes" if not totals else f"Tie: {', '.join(winners)}" if len(winners) > 1 else f"Winner: {winners[0]}"
        )
        lines.append(f"\n**Poll closed — {outcome}**")
    else:
        lines.append(f"\nCloses <t:{poll.deadline.timestamp()}:R>.")
    return "\n".join(lines)

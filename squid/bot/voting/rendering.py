"""Vote card rendering, independent of where the cards live.

Kept apart from the session classes so a card can be produced from a snapshot alone.
Rendering used to be a method that also fetched and edited its own messages, which is
why every session had to track the messages it had sent.
"""

import dataclasses
from collections.abc import Mapping
from textwrap import dedent

import discord

import squid_layouts as sl
from squid.bot.ui import DISCORD_GREEN, DISCORD_RED, DISCORD_YELLOW, CardField, card_layout, render_static
from squid.bot.voting.controls import poll_controls
from squid.voting.domain import VoteChoice, VoteSessionResult, VoteSessionSnapshot, VoteStatus


def primary_emoji(snapshot: VoteSessionSnapshot, choice: VoteChoice, guild_id: int | None = None) -> str:
    """Return the first configured emoji for a vote choice in one guild."""
    options = snapshot.options_for_guild(guild_id or 0)
    return next(option.emoji for option in options if option.choice is choice)


def render_build_review(
    card: sl.LayoutNode,
    snapshot: VoteSessionSnapshot,
    guild_id: int | None,
) -> discord.ui.LayoutView:
    """Compose a build card with the review vote state beneath it.

    The card arrives as IR rather than as a built container so the whole post is solved in
    one pass: the vote text competes for the display budget instead of being appended to an
    already-solved card and overflowing it.
    """
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
    # The vote state is Never: a review whose tallies were trimmed away is worse than a
    # review whose build description was.
    state = (sl.primitives.Sep(), sl.primitives.Text(vote_text, overflow=sl.primitives.Never()))
    # The build card is now sl.section()'s semantic Section rather than a bare primitive
    # Panel, so splice into its own children instead of nesting a second container — one
    # accent-coloured box, and the vote text is solved in the same pass as the card's fields.
    if isinstance(card, sl.semantic.Section):
        post: sl.LayoutNode = dataclasses.replace(card, children=(*card.children, *state))
    elif isinstance(card, sl.primitives.Panel):
        post = sl.primitives.Panel(children=(*card.children, *state), accent=card.accent)
    else:
        # Not currently reachable — render_node() always returns a Section — kept as a safe
        # fallback for any future card producer that returns something else entirely.
        post = sl.group(card, *state)
    return render_static([post])


def render_delete_log(snapshot: VoteSessionSnapshot, target_content: str) -> discord.ui.LayoutView:
    """Render the card asking whether a logged message should be deleted."""
    # Compare enum members rather than their string values: `status == "closed"` is true at
    # runtime for a StrEnum but reads as a non-overlapping comparison to a type checker, which
    # then treats every branch below `pending` as unreachable.
    match snapshot.result if snapshot.status is VoteStatus.CLOSED else VoteSessionResult.PENDING:
        case VoteSessionResult.PENDING:
            title = "Vote to Delete Log"
            action = (
                f"React with {primary_emoji(snapshot, VoteChoice.APPROVE)} to upvote or "
                f"{primary_emoji(snapshot, VoteChoice.DENY)} to downvote."
            )
            accent_colour = DISCORD_YELLOW
        case VoteSessionResult.APPROVED:
            title = "Vote to Delete Log: Passed"
            action = ""
            accent_colour = DISCORD_GREEN
        case VoteSessionResult.DENIED:
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


def render_generic_poll(
    snapshot: VoteSessionSnapshot,
    voter_discord_ids: Mapping[int, int] = {},
) -> discord.ui.LayoutView:
    """Render a user-created poll, honouring its visibility setting.

    An open poll carries its own close and refresh controls; a closed one has nothing left
    to do, so its card is inert and stays readable as a record.
    """
    nodes: list[sl.LayoutNode] = [sl.primitives.Text(generic_poll_text(snapshot, voter_discord_ids))]
    if snapshot.status is not VoteStatus.CLOSED:
        nodes.append(poll_controls())
    return render_static(nodes)


def generic_poll_text(snapshot: VoteSessionSnapshot, voter_discord_ids: Mapping[int, int] = {}) -> str:
    """The body of a generic poll card.

    *voter_discord_ids* maps a voting account to the snowflake to mention it by. A
    ballot records an account, not a snowflake, so the Discord spelling is supplied by
    the caller that can look it up; an account with no Discord identity is simply not
    mentioned.
    """
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
            voters = [
                f"<@{discord_id}>"
                for vote in snapshot.selections
                if vote.option_id == option.identifier
                and (discord_id := voter_discord_ids.get(vote.account_id)) is not None
            ]
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
        deadline = sl.md(t"{sl.timestamp(poll.deadline.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)}").content
        lines.append(f"\nCloses {deadline}.")
    return "\n".join(lines)

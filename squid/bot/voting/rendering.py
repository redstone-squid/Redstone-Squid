"""Render vote snapshots into cards without fetching or editing Discord messages."""

import dataclasses
from collections.abc import Mapping
from typing import cast

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.chrome import CHROME
from squid.bot.ui import CardField, card_node, render_payload
from squid.bot.voting.controls import poll_controls
from squid.bot.voting.poll_display import poll_choices, poll_question
from squid.core.voting.domain import VoteChoice, VoteOption, Voter, VoteSessionResult, VoteSessionSnapshot, VoteStatus
from squid_i18n import Text, discord_markup
from squid_ui.ext.discord import primitives
from squid_ui.ext.discord.target_types import ComponentsV2Target


def primary_emoji(snapshot: VoteSessionSnapshot, choice: VoteChoice, guild_id: int | None = None) -> str:
    """Return the first configured emoji for a vote choice in one guild."""
    menu = snapshot.menu
    return next(
        alias.emoji
        for alias in menu.aliases_for_guild(guild_id or 0)
        if (option := menu.option(alias.option_id)) is not None and option.choice is choice
    )


def _rows(snapshot: VoteSessionSnapshot, guild_id: int | None) -> list[tuple[VoteOption, str]]:
    """Return a poll's options with the emoji its owner guild selects them by.

    A poll drafted outside a guild has no aliases of its own, so it falls back to the
    unscoped aliases rather than to some arbitrary guild's palette.
    """
    menu = snapshot.menu
    return [
        (option, alias.emoji)
        for alias in menu.aliases_for_guild(guild_id or 0)
        if (option := menu.option(alias.option_id)) is not None
    ]


def render_build_review(
    card: sl.LayoutNode[ComponentsV2Target, sd.Request],
    snapshot: VoteSessionSnapshot,
    guild_id: int | None,
    submission_url: str | None = None,
) -> sd.messages.payload.MessagePayload:
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
        vote_text = sl.md(t"### Vote closed — {result_label}\n**Final score:** {snapshot.net_votes:g}")
    else:
        approve = sl.emoji_text(primary_emoji(snapshot, VoteChoice.APPROVE, guild_id))
        deny = sl.emoji_text(primary_emoji(snapshot, VoteChoice.DENY, guild_id))
        # A build review always threshold-closes, so both thresholds are set; the
        # nullable ones belong to generic polls, which never reach this renderer.
        assert snapshot.pass_threshold is not None
        assert snapshot.fail_threshold is not None
        fail_limit = -snapshot.fail_threshold
        vote_text = sl.md(
            t"### Vote in progress\n"
            t"React with {approve} to **accept** or {deny} to **deny**. Votes are anonymous.\n"
            t"**Accept:** {snapshot.upvotes:g}/{snapshot.pass_threshold}  •  "
            t"**Deny:** {snapshot.downvotes:g}/{fail_limit}"
        )
    # The vote state is Never: a review whose tallies were trimmed away is worse than a
    # review whose build description was.
    if submission_url:
        vote_text = Text.join("\n", [vote_text, sl.md(t"[View submission]({submission_url})")])
    state = (sd.v2.separator(), sd.v2.text(vote_text, overflow=primitives.Never()))
    # Add vote state to the card's children to keep one container and one shared
    # display budget for build fields and voting instructions.
    if isinstance(card, sl.semantic.Section):
        post: sl.LayoutNode[ComponentsV2Target, sd.Request] = dataclasses.replace(
            card, children=(*card.children, *state)
        )
    elif isinstance(card, primitives.Panel):
        children = cast(tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...], card.children)
        post = sd.v2.panel(*children, *state, accent=card.accent)
    else:
        # Other node types have no container children to extend.
        post = sl.group(card, *state)
    return render_payload([post])


def render_delete_log(snapshot: VoteSessionSnapshot, target_content: str) -> sd.messages.payload.MessagePayload:
    """Render the card asking whether a logged message should be deleted."""
    # Compare enum members rather than their string values: `status == "closed"` is true at
    # runtime for a StrEnum but reads as a non-overlapping comparison to a type checker, which
    # then treats every branch below `pending` as unreachable.
    match snapshot.result if snapshot.status is VoteStatus.CLOSED else VoteSessionResult.PENDING:
        case VoteSessionResult.PENDING:
            title = "Vote to Delete Log"
            approve = sl.emoji_text(primary_emoji(snapshot, VoteChoice.APPROVE))
            deny = sl.emoji_text(primary_emoji(snapshot, VoteChoice.DENY))
            action = sl.md(t"React with {approve} to upvote or {deny} to downvote.")
            accent_colour = sl.Tone.WARNING
        case VoteSessionResult.APPROVED:
            title = "Vote to Delete Log: Passed"
            action = sl.text.Text("")
            accent_colour = sl.Tone.SUCCESS
        case VoteSessionResult.DENIED:
            title = "Vote to Delete Log: Failed"
            action = sl.text.Text("")
            accent_colour = sl.Tone.DANGER
        case _:
            title = "Vote to Delete Log: Closed"
            action = sl.text.Text("")
            accent_colour = sl.Tone.WARNING

    content = discord_markup(target_content)
    description = sl.text.Text.join("\n\n", [part for part in (action, sl.md(t"**Log content**\n{content}")) if part])
    return render_payload(
        [
            card_node(
                title,
                description,
                accent_colour=accent_colour,
                fields=(
                    CardField("Upvotes", str(snapshot.upvotes)),
                    CardField("Downvotes", str(snapshot.downvotes)),
                    CardField("Net votes", str(snapshot.net_votes)),
                ),
            )
        ]
    )


def render_generic_poll(
    snapshot: VoteSessionSnapshot,
    voter_discord_ids: Mapping[Voter, int] = {},
) -> sd.messages.payload.MessagePayload:
    """Render a user-created poll, honouring its visibility setting.

    An open poll carries its own close and refresh controls; a closed one has nothing left
    to do, so its card is inert and stays readable as a record.
    """
    nodes = _generic_poll_nodes(snapshot, voter_discord_ids)
    if snapshot.is_open:
        nodes.append(poll_controls())
    return render_payload(nodes)


def _generic_poll_nodes(
    snapshot: VoteSessionSnapshot,
    voter_discord_ids: Mapping[Voter, int],
) -> list[sl.LayoutNode[ComponentsV2Target, sd.Request]]:
    """Build the semantic content for a generic poll without its controls."""
    poll = snapshot.poll
    assert poll is not None
    closed = snapshot.status is VoteStatus.CLOSED
    show_totals = poll.visibility != "anonymous_hidden" or closed
    rows = _rows(snapshot, poll.guild_id)
    nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [poll_question(poll.question)]

    if snapshot.status is VoteStatus.VOID:
        nodes.append(sl.status("Poll voided."))
        return nodes
    if snapshot.status in (VoteStatus.REVIEW, VoteStatus.COUNTING):
        if snapshot.status is VoteStatus.REVIEW:
            nodes.append(
                sl.status(
                    sl.md(t"Voting paused · Awaiting review. Staff can use /poll-review with poll ID {snapshot.id}.")
                )
            )
        else:
            nodes.append(sl.status("Voting paused · Counting votes"))
        return nodes
    if show_totals:
        raw = snapshot.raw_tallies()
        weighted = snapshot.weighted_tallies()
        tally_options: list[sp.TallyOption] = []
        for option, emoji in rows:
            option_id = option.id
            voters = [
                sl.mention(discord_id)
                for vote in snapshot.selections
                if vote.option_id == option.id and (discord_id := voter_discord_ids.get(vote.voter)) is not None
            ]
            voter_suffix: sl.TextLike = ""
            if poll.visibility == "visible_live" and voters:
                voter_names = sl.text.Text.join(", ", voters)
                voter_suffix = sl.text.Text(t" ({voter_names})")
            label = option.label or option.id
            weighted_count = weighted.get(option_id, 0)
            tally_options.append(
                sp.TallyOption(
                    option_id,
                    sl.md(t"{label} — {weighted_count:g} weighted{voter_suffix}"),
                    raw.get(option_id, 0),
                    emoji=emoji,
                )
            )
        nodes.append(sl.tally(tally_options, key="poll.tally", chrome=CHROME))
    else:
        nodes.append(poll_choices((option.id, option.label or option.id, emoji) for option, emoji in rows))

    if closed:
        weighted = snapshot.weighted_tallies()
        best = max(weighted.values(), default=0)
        winners = [option.label or option.id for option in snapshot.menu.options if weighted.get(option.id, 0) == best]
        outcome = (
            "No votes"
            if not weighted
            else f"Tie: {', '.join(winners)}"
            if len(winners) > 1
            else f"Winner: {winners[0]}"
        )
        if poll.voting_ended_at is not None:
            actual = sl.timestamp(poll.voting_ended_at.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)
            nodes.append(sl.note(sl.md(t"Closed {actual}.")))
            if (poll.voting_ended_at - poll.deadline).total("minutes") >= 15:
                scheduled = sl.timestamp(poll.deadline.to_stdlib())
                nodes.append(sl.note(sl.md(t"Scheduled for {scheduled}.")))
        nodes.append(sl.status(sl.md(t"Poll closed — {outcome}"), emphasis=sl.semantic.Emphasis.STRONG))
    else:
        nodes.append(
            sl.note(sl.md(t"Closes {sl.timestamp(poll.deadline.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)}."))
        )
    return nodes


def generic_poll_text(snapshot: VoteSessionSnapshot, voter_discord_ids: Mapping[Voter, int] = {}) -> str:
    """The body of a generic poll card.

    *voter_discord_ids* maps a voter to the snowflake to mention it by. An account's
    ballot records the account, not a snowflake, so the Discord spelling is supplied by
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
    for option, emoji in _rows(snapshot, poll.guild_id):
        line = f"{emoji} **{option.label}**"
        if show_totals:
            line += f" — {raw.get(option.id, 0)} votes, {weighted.get(option.id, 0):g} weighted"
        if poll.visibility == "visible_live" and show_totals:
            voters = [
                f"<@{discord_id}>"
                for vote in snapshot.selections
                if vote.option_id == option.id and (discord_id := voter_discord_ids.get(vote.voter)) is not None
            ]
            if voters:
                line += f" ({', '.join(voters)})"
        lines.append(line)
    if closed:
        totals = snapshot.weighted_tallies()
        best = max(totals.values(), default=0)
        winners = [option.label or option.id for option in snapshot.menu.options if totals.get(option.id, 0) == best]
        outcome = (
            "No votes" if not totals else f"Tie: {', '.join(winners)}" if len(winners) > 1 else f"Winner: {winners[0]}"
        )
        lines.append(f"\n**Poll closed — {outcome}**")
    else:
        deadline = sl.text.discord_text(
            sl.text.resolve_text(
                sl.plain(t"{sl.timestamp(poll.deadline.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)}"),
                sl.text.current_localization(),
            )
        )
        lines.append(f"\nCloses {deadline}.")
    return "\n".join(lines)

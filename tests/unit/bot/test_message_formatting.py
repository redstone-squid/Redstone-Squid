"""Authored formatting and copied message content survive the Discord rendering path."""

from types import SimpleNamespace

import discord
import pytest

import squid_ui_discord as sd
from squid.bot.starboard.render import starboard_layout
from squid.bot.voting.rendering import render_build_review, render_delete_log, render_generic_poll
from squid.messages.models import Message
from squid.starboard.application import EntryState
from squid.starboard.models import OriginMessage, StarboardConfig, StarboardEntry
from squid.voting.domain import VoteChoice, VoteKind, VoteOption, VoteSessionResult, VoteStatus
from squid_ui_discord.testing import MessageHarness
from tests.support.voting import build_snapshot, poll_snapshot


def text_displays(payload: sd.message_payload.MessagePayload) -> str:
    return "\n".join(
        item.content for item in payload.layout.walk_children() if isinstance(item, discord.ui.TextDisplay)
    )


@pytest.mark.parametrize("ping_author", [False, True])
def test_starboard_preserves_message_markup_and_formats_its_own_labels(ping_author: bool) -> None:
    message = MessageHarness(message_id=100, channel_id=30).source
    message.author = SimpleNamespace(
        id=42,
        name="author",
        display_name="*Author*",
        display_avatar=SimpleNamespace(url="https://example.org/avatar.png"),
    )
    message.content = "**bold** ||spoiler|| <:squid:123> <@456>\n  indented"
    message.reference = None
    message.attachments = []
    config = StarboardConfig(
        id=1, guild_id=10, channel_id=20, name="main", display_emoji="<a:star:789>", ping_author=ping_author
    )
    state = EntryState(
        config,
        OriginMessage(id=100, message=Message(id=100, channel_id=30, author_id=42), author_is_bot=False),
        StarboardEntry(starboard_id=1, origin_message_id=100, score=3, raw_count=3),
    )

    rendered = text_displays(starboard_layout(state, message))

    assert message.content in rendered
    assert r"**\*Author\***" in rendered
    assert ("<@42>" in rendered) is ping_author
    assert "<a:star:789> 3" in rendered
    assert "<#30>" in rendered


@pytest.mark.parametrize("closed", [False, True])
def test_delete_log_vote_preserves_the_original_message(closed: bool) -> None:
    content = "**original** <:squid:123> <@456>\n||spoiler||"
    snapshot = build_snapshot(
        kind=VoteKind.DELETE_LOG,
        status=VoteStatus.CLOSED if closed else VoteStatus.OPEN,
        result=VoteSessionResult.APPROVED if closed else VoteSessionResult.PENDING,
    )

    rendered = text_displays(render_delete_log(snapshot, content))

    assert "**Log content**\n" + content in rendered
    assert ("React with" in rendered) is not closed


def test_build_review_instructions_keep_custom_emoji_and_authored_markdown() -> None:
    snapshot = build_snapshot(
        options=(
            VoteOption("<:yes:123>", VoteChoice.APPROVE),
            VoteOption("<a:no:456>", VoteChoice.DENY),
        )
    )

    rendered = text_displays(render_build_review(sd.v2.panel(sd.v2.text("Build")), snapshot, None))

    assert "### Vote in progress" in rendered
    assert "<:yes:123> to **accept**" in rendered
    assert "<a:no:456> to **deny**" in rendered


def test_poll_tallies_keep_custom_emoji_and_literal_option_labels() -> None:
    snapshot = poll_snapshot(
        options=(
            VoteOption("<:yes:123>", VoteChoice.GENERIC, identifier="one", guild_id=10, label="**One**"),
            VoteOption("<a:no:456>", VoteChoice.GENERIC, identifier="two", guild_id=10, label="Two"),
        )
    )

    rendered = text_displays(render_generic_poll(snapshot))

    assert r"<:yes:123> \*\*One\*\*" in rendered
    assert "<a:no:456> Two" in rendered

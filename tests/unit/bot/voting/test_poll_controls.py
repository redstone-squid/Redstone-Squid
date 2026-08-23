"""The controls an open poll's card carries."""

import squid_layouts as sl
from squid.bot.voting.rendering import render_generic_poll
from squid.voting.domain import VoteSessionResult, VoteStatus
from tests.helpers.voting import poll_snapshot


def _custom_ids(presentation: sl.discord.presentation.DiscordPresentation) -> list[str]:
    """Every clickable custom id on a card, in render order.

    A dynamic item is not a `Button` — it wraps one — so the children are matched on
    carrying a custom id rather than on their type.
    """
    return [
        custom_id
        for child in presentation.layout.walk_children()
        if (custom_id := getattr(child, "custom_id", None))
    ]


def test_an_open_poll_card_carries_its_own_close_and_refresh_controls() -> None:
    """`/poll close` and `/poll refresh` made you paste a link to the card in front of you.

    Canonical ids are pinned rather than merely counted. Legacy ids are pinned separately
    against the route aliases, so new cards can migrate without orphaning old ones.
    """
    assert _custom_ids(render_generic_poll(poll_snapshot())) == ["r:polls:close", "r:polls:refresh"]


def test_a_closed_poll_card_has_nothing_left_to_click() -> None:
    closed = poll_snapshot(status=VoteStatus.CLOSED, result=VoteSessionResult.APPROVED)

    assert _custom_ids(render_generic_poll(closed)) == []

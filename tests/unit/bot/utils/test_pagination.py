"""The shared list paginator: what a page shows and who may turn it."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord

from squid.bot.utils.pagination import ListPaginator


def _paginator(count: int, *, page_size: int = 3, author_id: int = 7) -> ListPaginator:
    return ListPaginator(
        "Pending submissions",
        [f"entry {index}" for index in range(count)],
        author_id=author_id,
        empty="Nothing here.",
        page_size=page_size,
    )


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


def _interaction(user_id: int = 7) -> discord.Interaction[Any]:
    return cast(
        discord.Interaction[Any],
        cast(
            Any,
            SimpleNamespace(
                user=SimpleNamespace(id=user_id),
                message=None,
                response=SimpleNamespace(
                    edit_message=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=False)
                ),
            ),
        ),
    )


def test_a_single_page_carries_no_controls() -> None:
    """Two disabled buttons read as a broken control, not an absent one."""
    view = _paginator(2)

    assert not any(isinstance(child, discord.ui.Button) for child in view.walk_children())
    assert "Page 1 of" not in _text(view)


async def test_paging_moves_the_window_over_the_entries() -> None:
    view = _paginator(7)

    assert "entry 0" in _text(view)
    assert "entry 3" not in _text(view)

    await view.go_to(_interaction(), 1)

    assert "entry 3" in _text(view)
    assert "entry 0" not in _text(view)
    assert "Page 2 of 3" in _text(view)


async def test_paging_stops_at_the_ends() -> None:
    """Clamping, rather than wrapping: the buttons are disabled at the ends anyway,
    so a page index out of range can only come from a race worth ignoring."""
    view = _paginator(7)

    await view.go_to(_interaction(), -1)
    assert view.page == 0

    await view.go_to(_interaction(), 99)
    assert view.page == 2


async def test_the_controls_belong_to_whoever_asked() -> None:
    view = _paginator(7)
    interaction = _interaction(user_id=99)

    assert await view.interaction_check(interaction) is False
    assert await view.interaction_check(_interaction(user_id=7)) is True


def test_short_entries_can_read_as_a_run_rather_than_as_paragraphs() -> None:
    """`version list` is a list of tokens; a blank line between each would be absurd."""
    view = ListPaginator(
        "Recognized Java versions",
        ["1.20", "1.21"],
        author_id=7,
        empty="None yet.",
        separator=", ",
    )

    assert "1.20, 1.21" in _text(view)


def test_an_empty_list_says_so_instead_of_showing_a_blank_card() -> None:
    assert "Nothing here." in _text(_paginator(0))

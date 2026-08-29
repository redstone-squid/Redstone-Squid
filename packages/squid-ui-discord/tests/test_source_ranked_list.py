"""SourceRankedList: an asynchronous source paged through a live mount.

Every other machine in the library is tested without a transport, in `squid-ui-widgets`. This
one cannot be: what it is *about* is what the reader sees between asking for the next page and
the source answering -- a staged loading render, a followup edit, stale rows retained across a
failure. All three are mount behaviour, and none of them exists until a message has been sent.
"""

from dataclasses import dataclass

import discord
import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui.planning.navigation import NavigationContext, page_select_nav
from squid_ui.primitives import Button, Row
from squid_ui.sources import Position, Window
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord import testing as sd
from squid_ui_discord.testing import delivered_to, fake_interaction, fake_message


def _texts(view: discord.ui.View | discord.ui.LayoutView) -> list[str]:
    return sd.payload_texts(view)


def _labels(view: discord.ui.View | discord.ui.LayoutView) -> list[str]:
    return sd.payload_labels(view)


@dataclass(frozen=True)
class Score:
    name: str
    points: int


class ScoreSource:
    def __init__(
        self,
        entries: tuple[tuple[str, int], ...],
        *,
        capabilities: sl.sources.SourceCapabilities,
    ) -> None:
        self.entries = entries
        self.capabilities = capabilities
        self.requests: list[Position] = []

    async def fetch(self, position: Position, extent: int) -> Window[tuple[str, int]]:
        self.requests.append(position)
        keys = tuple(label for label, _score in self.entries)
        if position.anchor in keys:
            anchor = keys.index(position.anchor)
            if position.direction is sl.sources.Direction.FORWARD:
                offset = anchor + 1
            elif position.direction is sl.sources.Direction.BACKWARD:
                offset = max(0, anchor - extent)
            else:
                offset = anchor
        else:
            offset = position.offset
        visible = self.entries[offset : offset + extent]
        total = len(self.entries) if self.capabilities.count is not sl.sources.CountPrecision.NONE else None
        resolved_anchor = visible[0][0] if visible else None
        return Window(
            Position(resolved_anchor, offset),
            visible,
            has_previous=offset > 0 and self.capabilities.backward,
            has_next=offset + extent < len(self.entries),
            total=total,
        )


class FlakyScoreSource(ScoreSource):
    def __init__(self, entries: tuple[tuple[str, int], ...], *, capabilities: sl.sources.SourceCapabilities) -> None:
        super().__init__(entries, capabilities=capabilities)
        self.fail_next = False

    async def fetch(self, position: Position, extent: int) -> Window[tuple[str, int]]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("source unavailable")
        return await super().fetch(position, extent)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        (
            sl.sources.SourceCapabilities(offsets=True, jumpable=True, count=sl.sources.CountPrecision.EXACT),
            "-# Page 1 of 2",
        ),
        (sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.EXACT), "-# 1\N{EN DASH}2 of 3"),
        (
            sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.APPROXIMATE),
            "-# 1\N{EN DASH}2 of ~3",
        ),
        (sl.sources.SourceCapabilities(offsets=True), "-# 1\N{EN DASH}2"),
        (sl.sources.SourceCapabilities(), None),
    ],
)
async def test_source_ranked_list_gates_numeric_chrome_by_capability(
    capabilities: sl.sources.SourceCapabilities, expected: str | None
) -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=capabilities,
    )
    ranked = sp.SourceRankedList(source, key="leaderboard", identity=lambda entry: entry[0], page_size=2)
    message_root = MessageRoot(ranked, access=Everyone(), timeout=None)

    await message_root.send(delivered_to(fake_message()))

    assert message_root._view is not None
    numeric = [text for text in _texts(message_root._view) if text.startswith("-#")]
    assert numeric == ([] if expected is None else [expected])


async def test_source_ranked_list_fetches_in_handlers_and_uses_source_navigation() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5), ("Donald", 1)),
        capabilities=sl.sources.SourceCapabilities(
            backward=False,
            offsets=True,
            jumpable=True,
            count=sl.sources.CountPrecision.EXACT,
        ),
    )
    ranked = sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2)
    message_root = MessageRoot(ranked, access=Everyone(), timeout=None)
    await message_root.send(delivered_to(fake_message()))

    assert message_root._view is not None
    assert _labels(message_root._view) == ["Newer"]
    interaction = fake_interaction()
    await message_root.dispatch("stream.next", interaction)

    pending = interaction.response.edit_message.await_args.kwargs["view"]
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(pending)
    assert "-# Loading…" in _texts(pending)

    edited = interaction.followup.edit_message.await_args.kwargs["view"]
    assert source.requests[-1] == Position("Grace", 2, sl.sources.Direction.FORWARD)
    assert "3. **Edsger** — 10\n4. **Barbara** — 5" in _texts(edited)
    assert "-# Page 2 of 3" in _texts(edited)


async def test_source_ranked_list_retains_stale_rows_and_retries_the_failed_request() -> None:
    source = FlakyScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5)),
        capabilities=sl.sources.SourceCapabilities(
            backward=True,
            offsets=True,
            jumpable=True,
            count=sl.sources.CountPrecision.EXACT,
        ),
    )
    message_root = MessageRoot(
        sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
    )
    await message_root.send(delivered_to(fake_message()))

    source.fail_next = True
    failed_interaction = fake_interaction()
    await message_root.dispatch("stream.next", failed_interaction)

    failed = failed_interaction.followup.edit_message.await_args.kwargs["view"]
    assert "1. **Ada** — 30\n2. **Grace** — 20" in _texts(failed)
    assert "-# Could not load entries." in _texts(failed)
    assert "Retry" in _labels(failed)

    retry_interaction = fake_interaction()
    await message_root.dispatch("stream.retry", retry_interaction)

    pending = retry_interaction.response.edit_message.await_args.kwargs["view"]
    assert "-# Loading…" in _texts(pending)
    settled = retry_interaction.followup.edit_message.await_args.kwargs["view"]
    assert source.requests[-1] == Position("Grace", 2, sl.sources.Direction.FORWARD)
    assert "3. **Edsger** — 10\n4. **Barbara** — 5" in _texts(settled)


async def test_a_jumpable_source_seeks_by_page() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10), ("Barbara", 5), ("Donald", 1)),
        capabilities=sl.sources.SourceCapabilities(offsets=True, jumpable=True, count=sl.sources.CountPrecision.EXACT),
    )
    message_root = MessageRoot(
        sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=page_select_nav,
    )
    await message_root.send(delivered_to(fake_message()))

    interaction = fake_interaction()
    await message_root.dispatch("stream.seek", interaction, ["2"])

    # Page 2 of a page_size=2 source is item offset 4, not item offset 2.
    assert source.requests[-1] == Position(offset=4)
    pending = interaction.response.edit_message.await_args.kwargs["view"]
    assert "-# Loading…" in _texts(pending)

    edited = interaction.followup.edit_message.await_args.kwargs["view"]
    assert "-# Page 3 of 3" in _texts(edited)


async def test_a_sequential_source_offers_no_jump_control() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.EXACT),
    )
    seen: list[NavigationContext] = []

    def nav(context):
        seen.append(context)
        return page_select_nav(context)

    message_root = MessageRoot(
        sp.SourceRankedList(source, key="stream", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=nav,
    )
    await message_root.send(delivered_to(fake_message()))

    assert seen[-1].on_seek is None
    assert message_root._view is not None
    assert not [item for item in message_root._view.walk_children() if isinstance(item, discord.ui.Select)]


async def test_source_ranked_list_uses_the_message_root_navigation_factory() -> None:
    source = ScoreSource(
        (("Ada", 30), ("Grace", 20), ("Edsger", 10)),
        capabilities=sl.sources.SourceCapabilities(offsets=True, count=sl.sources.CountPrecision.EXACT),
    )
    seen = []

    def nav(context):
        seen.append(context.state)
        return (Row((Button("More", context.on_next, "more"),)),)

    message_root = MessageRoot(
        sp.SourceRankedList(source, key="leaderboard", identity=lambda entry: entry[0], page_size=2),
        access=Everyone(),
        timeout=None,
        nav=nav,
    )
    await message_root.send(delivered_to(fake_message()))

    assert seen[-1].key == "leaderboard"
    assert seen[-1].visible_range == (1, 2)
    assert seen[-1].total == 3
    assert message_root._view is not None and _labels(message_root._view) == ["More"]

"""Unit tests for the owner-only interaction bench: its routes, the lease, and seed survival."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.components import _component_factory  # pyright: ignore[reportPrivateUsage]

import squid.bot.testbench as testbench
import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.routes._root import router
from squid.bot.testbench import Bench, BenchFrame, Closable, enable_item, revive_seed
from squid_ui.primitives import Row, Text
from squid_ui_discord.testing import InteractionHarness, MessageHarness
from tests.support.discord import make_layout_bot

OWNER_ID = 42
INDEX_MESSAGE_ID = 1


@pytest.fixture
def bot() -> Any:
    return make_layout_bot(is_owner=AsyncMock(return_value=True))


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, Bench]]:
    """Register a `probe` that records its arguments and answers nothing."""
    calls: list[tuple[Any, Bench]] = []

    async def record(interaction: Any, bench: Bench) -> None:
        calls.append((interaction, bench))

    monkeypatch.setitem(testbench.PROBES, "probe", testbench.Probe("probe", "records", record))
    return calls


def _press(bot: Any, *, message_id: int = INDEX_MESSAGE_ID, user_id: int = OWNER_ID) -> InteractionHarness:
    harness = InteractionHarness(user_id, message_id=message_id, client=bot)
    posted = MessageHarness(message_id=message_id + 100)
    harness.channel.send = AsyncMock(return_value=posted.source)  # pyright: ignore[reportAttributeAccessIssue]
    return harness


def _buttons(view: discord.ui.LayoutView) -> list[dict[str, Any]]:
    return [c for row in view.to_components() if row["type"] == 1 for c in row["components"]]


async def test_index_seed_runs_the_named_probe(bot: Any, calls: list[tuple[Any, Bench]]) -> None:
    harness = _press(bot)

    await router.dispatch(harness.source, "r:bench:probe")

    (interaction, bench), *_ = calls
    assert interaction is harness.source
    assert (bench.name, bench.values, bench.leased) == ("probe", (), False)


async def test_leased_seed_and_select_mark_their_origin(bot: Any, calls: list[tuple[Any, Bench]]) -> None:
    await router.dispatch(_press(bot).source, "r:bench:probe:seed")
    await router.dispatch(
        _press(bot).source, "r:bench:probe:pick", component=sd.routing.RouteComponent.SELECT, values=("a", "b")
    )

    assert [(bench.leased, bench.values) for _, bench in calls] == [(True, ()), (False, ("a", "b"))]


async def test_non_owner_is_refused_before_the_probe(calls: list[tuple[Any, Bench]]) -> None:
    harness = _press(make_layout_bot(is_owner=AsyncMock(return_value=False)), user_id=7)

    await router.dispatch(harness.source, "r:bench:probe")

    assert calls == []
    assert [record.kwargs["ephemeral"] for record in harness.sends] == [True]


async def test_unanswered_probe_gets_a_summary_and_an_answered_one_does_not(bot: Any) -> None:
    silent = _press(bot)
    await router.dispatch(silent.source, "r:bench:lease")
    answered = _press(bot)
    await router.dispatch(answered.source, "r:bench:personal")

    assert len(silent.sends) == 1
    assert "`lease` · ok ·" in str(silent.sends[0].kwargs["view"].to_components())
    assert len(answered.sends) == 1
    assert "Only you can see this." in str(answered.sends[0].kwargs["view"].to_components())


async def test_unknown_probe_is_reported_not_raised(bot: Any) -> None:
    harness = _press(bot)

    await router.dispatch(harness.source, "r:bench:vanished")

    assert "No probe named `vanished`" in str(harness.sends[0].kwargs["view"].to_components())


async def test_static_lease_from_the_index_posts_with_the_seed_row_last(bot: Any) -> None:
    harness = _press(bot)

    await router.dispatch(harness.source, "r:bench:lease")

    send = harness.channel.send  # pyright: ignore[reportAttributeAccessIssue]
    send.assert_awaited_once()
    assert harness.message_harness.edits == []
    components = send.await_args.kwargs["view"].to_components()
    assert components[-1]["components"][0]["custom_id"] == "r:bench:lease:seed"


async def test_static_lease_from_a_leased_message_edits_in_place(bot: Any) -> None:
    harness = _press(bot, message_id=200)

    await router.dispatch(harness.source, "r:bench:lease:seed")

    harness.channel.send.assert_not_awaited()  # pyright: ignore[reportAttributeAccessIssue]
    (edit,) = harness.message_harness.edits
    assert _buttons(edit.kwargs["view"])[-1]["custom_id"] == "r:bench:lease:seed"


async def test_overfilled_static_lease_keeps_the_seed_row(bot: Any) -> None:
    harness = _press(bot)
    bench = Bench(harness.source, "probe", (), leased=False)

    await bench.lease(Text("x" * 5000))

    view = harness.channel.send.await_args.kwargs["view"]  # pyright: ignore[reportAttributeAccessIssue]
    assert _buttons(view)[-1]["custom_id"] == "r:bench:probe:seed"
    assert sum(len(c.get("content", "")) for c in view.to_components()) < 5000


def test_bench_frame_renders_the_seed_row_after_the_child() -> None:
    row = Row((Bench(MagicMock(), "finish", (), leased=False).seed(),))
    child = Closable()

    rendered = BenchFrame(child, row).render()

    assert isinstance(rendered[0], sl.primitives.Boundary)
    assert rendered[0].component is child
    assert rendered[1] is row


async def test_finishing_a_leased_mount_revives_the_seed(bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    revived = AsyncMock()
    monkeypatch.setattr(testbench, "revive_seed", revived)
    harness = _press(bot)

    result = await Bench(harness.source, "finish", (), leased=False).lease(Closable())

    assert isinstance(result, sd.Presented)
    revived.assert_not_awaited()
    await result.root.finish()
    posted = harness.channel.send.return_value  # pyright: ignore[reportAttributeAccessIssue]
    assert all(button["disabled"] for button in _buttons(posted.edits[-1].kwargs["view"]))
    revived.assert_awaited_once_with(posted, "r:bench:finish:seed")


async def test_lease_on_an_already_finished_root_revives_at_once(bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    revived = AsyncMock()
    monkeypatch.setattr(testbench, "revive_seed", revived)
    harness = _press(bot)
    posted = harness.channel.send.return_value  # pyright: ignore[reportAttributeAccessIssue]
    presented = await bot.app_ui.send(harness.channel, Closable(), access=sd.Everyone())
    assert isinstance(presented, sd.Presented)
    await presented.root.finish()

    await testbench._keep_seed_alive(presented, "r:bench:finish:seed")  # pyright: ignore[reportPrivateUsage]

    revived.assert_awaited_once_with(posted, "r:bench:finish:seed")


def _stale_message(fresh: Any) -> discord.Message:
    """A message whose channel fetches `fresh`; typed as the real thing for `revive_seed`."""
    return cast(
        discord.Message, SimpleNamespace(id=5, channel=SimpleNamespace(fetch_message=AsyncMock(return_value=fresh)))
    )


def _fresh_message(*buttons: tuple[str, bool]) -> Any:
    state = MagicMock()
    row: Any = {
        "type": 1,
        "id": 3,
        "components": [
            {"type": 2, "style": 2, "label": custom_id, "custom_id": custom_id, "disabled": disabled}
            for custom_id, disabled in buttons
        ],
    }
    components = [_component_factory({"type": 10, "id": 2, "content": "card"}, state), _component_factory(row, state)]
    return SimpleNamespace(id=5, components=components, edit=AsyncMock())


async def test_revive_seed_enables_only_the_seed_on_the_fetched_message() -> None:
    fresh = _fresh_message(("x:close", True), ("r:bench:finish:seed", True))
    stale = _stale_message(fresh)

    await revive_seed(stale, "r:bench:finish:seed")

    cast(Any, stale).channel.fetch_message.assert_awaited_once_with(5)
    view = fresh.edit.await_args.kwargs["view"]
    assert [(b["custom_id"], b["disabled"]) for b in _buttons(view)] == [
        ("x:close", True),
        ("r:bench:finish:seed", False),
    ]


async def test_revive_seed_does_not_edit_a_message_without_the_seed() -> None:
    fresh = _fresh_message(("x:close", True))
    stale = _stale_message(fresh)

    await revive_seed(stale, "r:bench:finish:seed")

    fresh.edit.assert_not_awaited()


def test_enable_item_reports_whether_it_found_the_seed() -> None:
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.ActionRow(discord.ui.Button(custom_id="seed", disabled=True)))

    assert enable_item(view, "seed") is True
    assert enable_item(view, "other") is False
    assert _buttons(view)[0]["disabled"] is False


def test_index_lists_every_probe_and_the_gone_id() -> None:
    from squid.bot.ui import render_payload

    view = render_payload(testbench.index_nodes(), strict=True).view
    assert isinstance(view, discord.ui.LayoutView)
    ids = {button["custom_id"] for button in _buttons(view)}

    assert {f"r:bench:{name}" for name in testbench.PROBES} <= ids
    assert testbench.GONE_CUSTOM_ID in ids
    selects = [c for row in view.to_components() if row["type"] == 1 for c in row["components"] if c["type"] == 3]
    assert [s["custom_id"] for s in selects] == ["r:bench:echo:pick"]

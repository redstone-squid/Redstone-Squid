"""Browsing through mounted Discord actions."""

import anyio
import pytest

import squid_ui_widgets as sp
from squid_ui.ext.discord.testing import stage
from squid_ui.sources import Batch, Position, SourceCapabilities, list_source


async def test_detail_steps_cross_windows_and_return_to_the_containing_overview() -> None:
    browser = sp.Browser(
        list_source(("a", "b", "c")), identity=str, label=str, detail=lambda item: f"Detail {item}", fetch_size=2
    )
    async with stage(browser, owner=object()) as ui:
        await ui.select(ui.key_for("browser.open"), ["b"])
        assert "Detail b" in ui.texts()
        await ui.press(ui.key_for("browser.item-next"))
        assert "Detail c" in ui.texts()
        await ui.press(ui.key_for("browser.item-previous"))
        assert "Detail b" in ui.texts()
        await ui.press(ui.key_for("browser.back"))
        assert "a" in " ".join(ui.texts())


async def test_back_reveals_the_focused_item_without_overriding_later_layout_page_turns() -> None:
    browser = sp.Browser(
        list_source(tuple(str(index) for index in range(8))),
        identity=str,
        label=str,
        summary=lambda item: f"Entry {item}: " + "content " * 150,
        detail=lambda item: f"Detail {item}",
        fetch_size=8,
    )
    async with stage(browser, owner=object()) as ui:
        await ui.select(ui.key_for("browser.open"), ["7"])
        assert "Detail 7" in ui.texts()
        await ui.press(ui.key_for("browser.back"))
        assert any("Entry 7:" in text for text in ui.texts())
        before = ui.root.presentation.cursor("browser.items").position.page
        assert before > 0
        await ui.press(ui.key_for("__cursor_previous.browser.items"))
        assert ui.root.presentation.cursor("browser.items").position.page == before - 1


async def test_back_during_a_boundary_fetch_does_not_reopen_the_fetched_detail() -> None:
    class Source:
        capabilities = SourceCapabilities(backward=True)

        def __init__(self) -> None:
            self.started = anyio.Event()
            self.release = anyio.Event()

        async def fetch(self, position: Position, fetch_size: int) -> Batch[str]:
            if position.anchor is None:
                return Batch(Position(), ("a",), next=Position("b"))
            self.started.set()
            await self.release.wait()
            return Batch(Position("b"), ("b",), previous=Position())

    source = Source()
    browser = sp.Browser(source, identity=str, label=str, detail=lambda item: f"Detail {item}", fetch_size=1)
    async with stage(browser, owner=object()) as ui:
        await ui.select(ui.key_for("browser.open"), ["a"])
        with anyio.fail_after(3):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(ui.press, ui.key_for("browser.item-next"))
                await source.started.wait()
                tasks.start_soon(ui.press, ui.key_for("browser.back"))
                await anyio.wait_all_tasks_blocked()
                assert browser.session.focus is None
                source.release.set()
        assert browser.session.snapshot is not None
        assert browser.session.snapshot.loaded.batch.items == ("a",)
        assert not any("Detail" in text for text in ui.texts())


async def test_failed_back_delivery_keeps_the_reveal_pending_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = sp.Browser(
        list_source(tuple(str(index) for index in range(8))),
        identity=str,
        label=str,
        summary=lambda item: f"Entry {item}: " + "content " * 150,
        detail=lambda item: f"Detail {item}",
        fetch_size=8,
    )
    async with stage(browser, owner=object()) as ui:
        await ui.select(ui.key_for("browser.open"), ["7"])
        previous = ui.root.presentation.cursor("browser.items").position
        with monkeypatch.context() as patch:

            async def failed_delivery(*args: object, **kwargs: object) -> None:
                raise RuntimeError("delivery failed")

            patch.setattr(ui.root, "_deliver", failed_delivery)
            with pytest.raises(RuntimeError, match="delivery failed"):
                await ui.press(ui.key_for("browser.back"))
        assert ui.root.presentation.cursor("browser.items").position == previous
        assert ui.root.presentation.pending_reveals
        assert "Detail 7" in ui.texts()
        await ui.root.refresh()
        assert any("Entry 7:" in text for text in ui.texts())
        assert not ui.root.presentation.pending_reveals


async def test_forward_flow_retries_a_split_item_without_advancing_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[Position] = []

    class Source:
        capabilities = SourceCapabilities()

        async def fetch(self, position: Position, fetch_size: int) -> Batch[str]:
            requests.append(position)
            assert fetch_size == 2
            if position == Position():
                return Batch(position, ("A", "B"), next=Position("C"))
            assert position == Position("C")
            return Batch(position, ("C", "D"))

    flow = sp.SourceFlow(
        Source(),
        identity=str,
        paragraphs=lambda item: ("C1", "C2") if item == "C" else (item,),
        max_paragraphs_per_page=3,
        fetch_size=2,
    )
    async with stage(flow, owner=object()) as ui:
        first = " ".join(ui.texts())
        assert all(text in first for text in ("A", "B", "C1", "Page 1"))
        assert "C2" not in first
        key = ui.key_for("flow.next")
        with monkeypatch.context() as patch:

            async def fail(*args: object, **kwargs: object) -> None:
                raise RuntimeError("delivery failed")

            patch.setattr(ui.root, "_deliver", fail)
            with pytest.raises(RuntimeError, match="delivery failed"):
                await ui.press(key)
        assert " ".join(ui.texts()) == first
        await ui.press(key)
        second = " ".join(ui.texts())
        assert all(text in second for text in ("C2", "D", "Page 2"))
        assert "C1" not in second
        assert requests == [Position(), Position("C")]

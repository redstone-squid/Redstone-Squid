"""Collection focus and async movement share one accepted revision."""

import asyncio

import anyio
import pytest

from squid_reactivity import transaction
from squid_reactivity.operations import Failed
from squid_ui.sources import LoadedWindow, Position, SourceCapabilities, Window, list_source
from squid_ui_widgets.browsing import BrowseSession


async def settle(session: BrowseSession[str]) -> None:
    execution = session.operation
    if execution is not None:
        await execution


async def test_item_steps_cross_windows_and_back_returns_to_the_containing_window() -> None:
    session = BrowseSession(list_source(("a", "b", "c")), identity=str, page_size=2)
    await settle(session)
    session.open("a", revision=session.revision)
    session.move("next-item", revision=session.revision)
    assert session.focused_item == "b"
    session.move("next-item", revision=session.revision)
    assert session.focused_item == "b"
    await settle(session)
    assert session.focused_item == "c"
    assert not session.can_step(1)
    assert session.can_step(-1)
    session.move("previous-item", revision=session.revision)
    await settle(session)
    assert session.focused_item == "b"
    session.back(revision=session.revision)
    assert session.snapshot is not None
    assert session.snapshot.focus is None
    assert session.snapshot.loaded.window.items == ("a", "b")
    assert session.snapshot.reveal == "b"


class HeldSource:
    capabilities = SourceCapabilities(backward=True)

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False
        self.requests: list[Position] = []

    async def fetch(self, position: Position, extent: int) -> Window[str]:
        self.requests.append(position)
        if position.anchor is None:
            return Window(Position(), ("a",), next=Position("second"))
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("offline")
        return Window(Position("second"), ("b",), previous=Position())


@pytest.mark.parametrize("replacement", ["back", "reset"])
async def test_a_late_result_cannot_reopen_detail_or_replace_a_new_collection(replacement: str) -> None:
    source = HeldSource()
    session = BrowseSession(source, identity=str, page_size=1)
    await settle(session)
    session.open("a", revision=session.revision)
    session.move("next-item", revision=session.revision)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(settle, session)
        await source.started.wait()
        if replacement == "back":
            session.back(revision=session.revision)
        else:
            session.reset(list_source(("x",)))
            await settle(session)
        source.release.set()
    assert session.snapshot is not None
    assert session.snapshot.focus is None
    assert session.snapshot.loaded.window.items == (("a",) if replacement == "back" else ("x",))


async def test_retry_preserves_the_original_destination_and_focus() -> None:
    source = HeldSource()
    source.release.set()
    source.fail = True
    session = BrowseSession(source, identity=str)
    await settle(session)
    session.open("a", revision=session.revision)
    session.move("next-item", revision=session.revision)
    with pytest.raises(RuntimeError, match="offline"):
        await settle(session)
    assert session.operation is not None and isinstance(session.operation.status, Failed)
    assert session.focused_item == "a"
    source.fail = False
    session.retry(revision=session.revision)
    await settle(session)
    assert session.focused_item == "b"
    assert source.requests[-2:] == [Position("second"), Position("second")]


async def test_stale_and_duplicate_clicks_cannot_skip_items() -> None:
    session = BrowseSession(list_source(("a", "b", "c")), identity=str, page_size=1)
    await settle(session)
    session.open("a", revision=session.revision)
    revision = session.revision
    session.move("next-item", revision=revision)
    pending = session.operation
    session.move("next-item", revision=revision)
    assert session.operation is pending
    await settle(session)
    session.move("next-item", revision=revision)
    assert session.focused_item == "b"


async def test_a_rolled_back_navigation_action_does_not_start_work() -> None:
    session = BrowseSession(list_source(("a", "b")), identity=str, page_size=1)
    await settle(session)
    before = session.snapshot
    with pytest.raises(RuntimeError, match="rollback"), transaction():
        session.move("next", revision=session.revision)
        raise RuntimeError("rollback")
    assert session.operation is None
    assert session.snapshot is before


async def test_forward_only_sources_still_allow_local_previous_item() -> None:
    source = HeldSource()
    source.capabilities = SourceCapabilities()
    session = BrowseSession(source, identity=str)
    session.replace(LoadedWindow(Window(Position(), ("a", "b")), "ab"))
    session.open("b", revision=session.revision)
    assert session.can_step(-1)
    session.move("previous-item", revision=session.revision)
    assert session.focused_item == "a"
    assert not session.can_step(-1)

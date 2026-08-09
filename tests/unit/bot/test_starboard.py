import asyncio

from pytest_mock import MockerFixture

from squid.bot.starboard.debounce import EntryDebouncer


async def test_starboard_debouncer_coalesces_and_preserves_force() -> None:
    calls: list[tuple[tuple[int, int], bool]] = []

    async def callback(key: tuple[int, int], force: bool) -> None:
        calls.append((key, force))

    debouncer = EntryDebouncer(callback, delay=0)
    debouncer.schedule((1, 2))
    debouncer.schedule((1, 2), force=True)
    await debouncer.drain()

    assert calls == [((1, 2), True)]


async def test_starboard_debouncer_isolates_keys() -> None:
    called: set[tuple[int, int]] = set()

    async def callback(key: tuple[int, int], force: bool) -> None:
        called.add(key)
        await asyncio.sleep(0)

    debouncer = EntryDebouncer(callback, delay=0)
    debouncer.schedule((1, 2))
    debouncer.schedule((1, 3))
    await debouncer.drain()

    assert called == {(1, 2), (1, 3)}


async def test_starboard_debouncer_traces_each_refresh(mocker: MockerFixture) -> None:
    async def callback(key: tuple[int, int], force: bool) -> None:
        pass

    span_context = mocker.MagicMock()
    trace = mocker.patch("squid.bot.starboard.debounce.trace_span", return_value=span_context)
    debouncer = EntryDebouncer(callback, delay=0)

    debouncer.schedule((1, 2))
    await debouncer.drain()

    trace.assert_called_once_with(
        "squid.background.starboard_refresh",
        {"squid.surface": "background_work"},
    )


async def test_starboard_debouncer_cancels_pending_work_on_close() -> None:
    called = False

    async def callback(key: tuple[int, int], force: bool) -> None:
        nonlocal called
        called = True

    debouncer = EntryDebouncer(callback, delay=60)
    debouncer.schedule((1, 2), force=True)

    await debouncer.close()
    debouncer.schedule((1, 3))
    await asyncio.sleep(0)

    assert called is False

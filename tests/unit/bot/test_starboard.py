import asyncio

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

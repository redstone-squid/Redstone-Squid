"""Starboard refresh coalescing and owned task lifetimes."""

import anyio

from squid.bot.starboard.debounce import EntryDebouncer, EntryKey
from squid.runtime import BackgroundTaskSupervisor


async def test_debouncer_coalesces_each_key_and_preserves_force() -> None:
    calls: list[tuple[EntryKey, bool]] = []

    async def callback(key: EntryKey, force: bool) -> None:
        await anyio.lowlevel.checkpoint()
        calls.append((key, force))

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2))
        debouncer.schedule((1, 3))
        debouncer.schedule((1, 2), force=True)
        debouncer.schedule((1, 2))
        await debouncer.drain()

    assert sorted(calls) == [((1, 2), True), ((1, 3), False)]


async def test_activity_during_refresh_runs_once_more_with_latest_state_and_force() -> None:
    started = anyio.Event()
    release = anyio.Event()
    current = 1
    published: list[tuple[int, bool]] = []

    async def callback(key: EntryKey, force: bool) -> None:
        value = current
        if not published:
            started.set()
            await release.wait()
        published.append((value, force))

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2))
        with anyio.fail_after(1):
            await started.wait()
            current = 2
            debouncer.schedule((1, 2), force=True)
            current = 3
            debouncer.schedule((1, 2))
            release.set()
            await debouncer.drain()

        assert published == [(1, False), (3, True)]
        current = 4
        debouncer.schedule((1, 2))
        await debouncer.drain()

    assert published == [(1, False), (3, True), (4, False)]


async def test_close_settles_inflight_refresh_and_cancels_pending_work() -> None:
    calls: list[EntryKey] = []
    started = anyio.Event()
    finished = anyio.Event()

    async def callback(key: EntryKey, force: bool) -> None:
        calls.append(key)
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            finished.set()

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2), force=True)
        with anyio.fail_after(1):
            await started.wait()
            debouncer.schedule((1, 2), force=True)
            debouncer.schedule((1, 3))
            await debouncer.close()

        assert finished.is_set()
        debouncer.schedule((1, 4))
        await debouncer.close()
        await anyio.lowlevel.checkpoint()

    assert calls == [(1, 2)]


async def test_failed_refresh_does_not_block_other_keys_or_later_attempts() -> None:
    calls: list[tuple[EntryKey, bool]] = []
    started = anyio.Event()
    release = anyio.Event()

    async def callback(key: EntryKey, force: bool) -> None:
        calls.append((key, force))
        if key == (1, 2) and force:
            started.set()
            await release.wait()
            raise RuntimeError("Discord unavailable")

    async with BackgroundTaskSupervisor().running() as supervisor:
        debouncer = EntryDebouncer(callback, supervisor, delay=0)
        debouncer.schedule((1, 2), force=True)
        debouncer.schedule((1, 3))
        with anyio.fail_after(1):
            await started.wait()
            debouncer.schedule((1, 2))
            release.set()
            await debouncer.drain()

    assert sorted(calls) == [((1, 2), False), ((1, 2), True), ((1, 3), False)]

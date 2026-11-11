"""Application UI tests run standalone panels under a supervised service host."""

import inspect
from collections.abc import Iterator
from functools import wraps
from typing import Any

import anyio
import pytest

from squid_ui.runtime import UIExecutionHost
from squid_ui_discord import MessageRoot


@pytest.fixture(autouse=True)
def _host_standalone_roots(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply standalone test roots with a host owned by the test coroutine.

    Pytest-asyncio enters and exits async fixtures in different tasks, so the task
    group wraps the test body instead. Explicit execution_host arguments, including
    None in missing-host tests, always take precedence over this harness default.
    """
    test = request.node.obj
    if not inspect.iscoroutinefunction(test):
        return
    host = UIExecutionHost()
    original = MessageRoot.__init__

    @wraps(original)
    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        if "execution_host" not in kwargs:
            config = kwargs.get("config")
            configured = None if config is None else config.execution_host
            if configured is None or configured._tasks is None:
                kwargs["execution_host"] = host
        original(self, *args, **kwargs)

    @wraps(test)
    async def hosted(*args: Any, **kwargs: Any) -> None:
        async with anyio.create_task_group() as tasks:
            await tasks.start(host.run)
            try:
                await test(*args, **kwargs)
            finally:
                await host.close()

    monkeypatch.setattr(MessageRoot, "__init__", initialize)
    monkeypatch.setattr(request.node, "obj", hosted)


@pytest.fixture(autouse=True)
def _fail_on_missed_redraws(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail a test in which a message changed from outside but could not redraw.

    MessageRoot logs this when a screen without live_updates is invalidated by anything but its
    own interactions; in production the change would stay invisible until the next press.
    """
    yield
    missed = [
        record.getMessage()
        for phase in ("setup", "call")
        for record in caplog.get_records(phase)
        if getattr(record, "squid_unscheduled_change", False)
    ]
    assert not missed, "\n".join(missed)

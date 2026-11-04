"""Isolate package registries and provide execution hosts for standalone test roots."""

import inspect
import os
from collections.abc import Iterator
from functools import wraps
from typing import Any

import anyio
import pytest
from hypothesis import settings

from squid_ui.runtime import UIExecutionHost
from squid_ui_discord import MessageRoot, live, runtime

settings.register_profile(
    "ci",
    max_examples=500,
    deadline=None,
    print_blob=True,
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def _isolated_process_state() -> Iterator[None]:
    """Clear process-wide registries before and after each test.

    Weak references can outlive a test until collection, so relying on garbage collection
    would make registry assertions depend on test order.
    """
    live._LIVE.clear()
    runtime._INSTALLED.clear()
    yield
    live._LIVE.clear()
    runtime._INSTALLED.clear()


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

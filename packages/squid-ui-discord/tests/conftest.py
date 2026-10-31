"""Package-local test configuration; the repo-root conftest does not reach this directory."""

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
    """Start and leave behind an empty live registry and no installed runtimes.

    Both registries are process-wide, and both are weak collections -- so a leak is bounded by
    when the garbage collector gets around to the message root or client, not by the end of the
    test that made it. A test that holds a reference past its own body therefore reaches the
    next one, which is exactly the shape of an order-dependent failure nobody can reproduce in
    isolation. Six files used to carry half of this fixture each; two that install clients
    carried neither.
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

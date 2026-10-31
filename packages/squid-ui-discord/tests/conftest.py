"""Package-local test configuration; the repo-root conftest does not reach this directory."""

import os
from collections.abc import Iterator

import pytest
from hypothesis import settings

from squid_ui_discord import live, runtime

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

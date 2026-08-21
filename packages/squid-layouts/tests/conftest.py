"""Package-local test configuration; the repo-root conftest does not reach this directory."""

import os

import pytest
from hypothesis import settings

from squid_layouts import strict_state

settings.register_profile(
    "ci",
    max_examples=500,
    deadline=None,
    print_blob=True,
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def _strict_state():
    """Undeclared component writes are a test failure, not a log line."""
    with strict_state():
        yield

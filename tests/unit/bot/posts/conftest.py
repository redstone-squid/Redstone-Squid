"""Post rendering and reconciliation usually run from a queued refresh."""

from collections.abc import Iterator

import pytest

from squid.core.actors import ActingContext


@pytest.fixture(autouse=True)
def reconciliation_context() -> Iterator[None]:
    with ActingContext.worker("discord.post-reconciliation").scope():
        yield

"""Shared contracts for the unified entity models."""

import pytest

from squid.persistence.base import Base
from squid.posts.models import DiscordPost


@pytest.mark.parametrize("entity", [DiscordPost])
def test_unified_entities_use_object_identity(entity: type[Base]) -> None:
    """Include each migrated entity here until the shared base adopts this contract."""
    assert entity.__eq__ is object.__eq__
    assert entity.__hash__ is object.__hash__

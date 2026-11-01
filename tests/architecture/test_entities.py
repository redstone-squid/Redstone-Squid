"""Shared contracts for the unified entity models."""

import pytest

from squid.persistence import model_registry  # noqa: F401
from squid.persistence.base import Base


@pytest.mark.parametrize(
    "entity", sorted((mapper.class_ for mapper in Base.registry.mappers), key=lambda cls: cls.__name__)
)
def test_unified_entities_use_object_identity(entity: type[Base]) -> None:
    """Every mapped entity inherits identity equality and hashing."""
    assert entity.__eq__ is object.__eq__
    assert entity.__hash__ is object.__hash__

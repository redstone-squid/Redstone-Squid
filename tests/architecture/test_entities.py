"""Shared contracts for the unified entity models."""

from typing import Any

import pytest
from sqlalchemy.orm import Mapper

from squid.persistence import model_registry  # noqa: F401
from squid.persistence.base import Base


@pytest.mark.parametrize(
    "entity", sorted((mapper.class_ for mapper in Base.registry.mappers), key=lambda cls: cls.__name__)
)
def test_unified_entities_use_object_identity(entity: type[Base]) -> None:
    """Every mapped entity inherits identity equality and hashing."""
    assert entity.__eq__ is object.__eq__
    assert entity.__hash__ is object.__hash__


@pytest.mark.parametrize("mapper", sorted(Base.registry.mappers, key=lambda mapper: mapper.class_.__name__))
def test_entity_relationships_require_explicit_loading(mapper: Mapper[Any]) -> None:
    for relationship in mapper.relationships:
        assert relationship.lazy in {"raise", "raise_on_sql"}, relationship

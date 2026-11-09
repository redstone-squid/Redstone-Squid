"""Explicit unit hydration for tag values rendered outside a database session."""

from sqlalchemy.orm import Load

from squid.tags.models import TagAssignment, TagDefinition


def definition_units() -> tuple[Load, ...]:
    return (
        Load(TagDefinition).joinedload(TagDefinition.canonical_unit_definition),
        Load(TagDefinition).joinedload(TagDefinition.default_display_unit_definition),
    )


def assignment_units() -> tuple[Load, ...]:
    return (
        Load(TagAssignment).joinedload(TagAssignment.display_unit_definition),
        Load(TagAssignment).joinedload(TagAssignment.definition).options(*definition_units()),
    )

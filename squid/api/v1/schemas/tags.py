"""Public tag-definition representations."""

from decimal import Decimal
from typing import Self

from pydantic import ConfigDict

from squid.api.v1.schemas import FromDomain
from squid.tags.domain import TagDefinition


class TagDetail(FromDomain[TagDefinition]):
    """A published tag clients may use in build and search views."""

    model_config = ConfigDict(extra="forbid")

    id: int
    key: str
    name: str
    query_name: str | None
    authority: str
    kind: str
    value_type: str
    restriction_type: str | None
    record_operator: str | None
    canonical_unit: str | None
    display_unit: str | None
    numeric_quantum: Decimal | None

    @classmethod
    def from_domain(cls, definition: TagDefinition, /) -> Self:
        return cls(
            id=definition.id,
            key=definition.stable_key,
            name=definition.display_name,
            query_name=definition.query_name,
            authority=definition.authority.value,
            kind=definition.semantic_kind.value,
            value_type=definition.value_type.value,
            restriction_type=definition.restriction_type,
            record_operator=definition.record_operator.value if definition.record_operator is not None else None,
            canonical_unit=definition.canonical_unit,
            display_unit=definition.default_display_unit,
            numeric_quantum=definition.numeric_quantum,
        )

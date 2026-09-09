"""Public tag-definition representations."""

from decimal import Decimal
from typing import Self

from pydantic import ConfigDict, Field

from squid.api.v1.schemas import FromDomain
from squid.tags.domain import TagDefinition


class TagDetail(FromDomain[TagDefinition]):
    """A published tag clients may use in build and search views.

    Only approved tags are published, and the unit and step fields are set on numeric tags alone.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    key: str = Field(description="Stable identifier, safe to hardcode; `name` is not.")
    name: str = Field(description="Display name, which staff may change.")
    query_name: str | None = Field(
        description="Lowercase snake_case name accepted in search queries. Null when the tag is not queryable."
    )
    authority: str = Field(
        description="`official` for a staff-defined tag, `user` for a member-defined one. Only official tags are "
        "restrictions or patterns."
    )
    kind: str = Field(
        description="What the tag asserts: `restriction` for a rule the build obeys, `pattern` for one it uses, "
        "`showcase` for a free-form label."
    )
    value_type: str = Field(
        description="Type an assignment carries: `none`, `numeric`, `text` or `boolean`. A `none` tag is a bare label."
    )
    restriction_type: str | None = Field(description="Set exactly when `kind` is `restriction`, and null otherwise.")
    record_operator: str | None = Field(
        description="How an assignment satisfies a record predicate: `present`, `exact`, `at_most` or `at_least`. "
        "Always null when `kind` is `showcase`."
    )
    canonical_unit: str | None = Field(description="Unit assigned values are stored in.")
    display_unit: str | None = Field(description="Unit to present values in by default, which may differ from storage.")
    numeric_step: Decimal | None = Field(description="Granularity assigned values snap to.")

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
            numeric_step=definition.numeric_step,
        )

"""Versioned concrete computation inputs, detached from database sessions."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator, model_validator

from squid.records.application.models import CandidateBuild, CandidateFacet, CategoryIdentity
from squid.records.domain.models import BuildKind


class RecordSnapshot(BaseModel):
    """The complete candidate population and requested categories for one scope.

    Capture time is also the evaluation time, so replaying saved inputs does not
    introduce clock-dependent differences in configuration evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    rules_revision_id: int
    kind: BuildKind
    version_id: int | None
    sources: tuple[CandidateBuild, ...]
    requested: tuple[CategoryIdentity, ...]
    captured_at: datetime

    @field_validator("sources", mode="before")
    @classmethod
    def parse_sources(cls, value: object) -> tuple[CandidateBuild, ...]:
        # Python-mode union validation preserves numeric-looking text values.
        return TypeAdapter(tuple[CandidateBuild, ...]).validate_python(value)

    @model_validator(mode="after")
    def restore_numeric_facets(self) -> Self:
        # JSON represents Decimal as a string. The facet's declared value type
        # distinguishes numeric thresholds from actual text values.
        def restore(facet: CandidateFacet) -> CandidateFacet:
            if facet.value_type != "numeric":
                return facet
            return replace(
                facet,
                assigned_value=Decimal(facet.assigned_value)
                if isinstance(facet.assigned_value, str)
                else facet.assigned_value,
                category_value=Decimal(facet.category_value)
                if isinstance(facet.category_value, str)
                else facet.category_value,
            )

        object.__setattr__(
            self,
            "sources",
            tuple(
                replace(
                    source,
                    restrictions=tuple(restore(f) for f in source.restrictions),
                    types=tuple(restore(f) for f in source.types),
                )
                for source in self.sources
            ),
        )
        return self

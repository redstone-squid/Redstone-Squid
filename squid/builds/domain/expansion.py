"""Source-stated expandable-door facts, independent of any arithmetic solver.

Expressions retain source syntax and are not executable or computationally
validated. Missing bounds and steps remain unknown. Formula branches describe
alternative constructions or phases; their overlap is not resolved at ingestion.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

_INVALID_VALUE = "value_error"

SourceText = Annotated[str, Field(min_length=1, max_length=10_000)]
PositiveInteger = Annotated[int, Field(strict=True, ge=1)]


class ExpansionFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpansionEvidence(ExpansionFact):
    """A source excerpt supporting the surrounding expansion facts."""

    source_message_id: PositiveInteger
    text: SourceText


class ExpansionParameter(ExpansionFact):
    """A named parameter's meaning and optional integer progression.

    minimum is the starting value when steps are supplied. initial_steps happen
    once, then repeating_steps cycle. None means the sequence was not specified;
    an empty initial_steps tuple explicitly means no initial exceptions.
    """

    name: Annotated[str, Field(min_length=1, max_length=64)]
    meaning: SourceText | None = None
    minimum: int | None = Field(default=None, strict=True, ge=0)
    maximum_kind: Literal["unknown", "finite", "unbounded"] = "unknown"
    maximum: int | None = Field(default=None, strict=True, ge=0)
    initial_steps: tuple[PositiveInteger, ...] | None = Field(default=None, max_length=64)
    repeating_steps: tuple[PositiveInteger, ...] | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if (self.maximum_kind == "finite") != (self.maximum is not None):
            msg = "A finite maximum requires a value; unknown and unbounded maxima must omit it."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            msg = "The minimum cannot exceed the maximum."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        return self


class ExpansionTiming(ExpansionFact):
    """A source timing formula; its units and variant are not inferred from a sample."""

    method: Literal["opening", "opening_visible", "closing", "closing_visible", "opening_reset", "closing_reset"]
    expression: SourceText
    unit: Literal["game_tick", "redstone_tick", "second", "unknown"] = "unknown"
    variant: SourceText | None = None


class ExpansionBranch(ExpansionFact):
    """One construction or phase, including any source-claimed record domain."""

    label: SourceText | None = None
    opening_width: SourceText | None = None
    opening_height: SourceText | None = None
    opening_depth: SourceText | None = None
    domain: SourceText | None = None
    volume: SourceText | None = None
    timings: tuple[ExpansionTiming, ...] = Field(default=(), max_length=64)
    claimed_record_domain: SourceText | None = None
    completion_time: SourceText | None = None
    evidence: tuple[ExpansionEvidence, ...] = Field(default=(), max_length=64)


class DoorExpansion(ExpansionFact):
    """Versioned source facts retained even when too incomplete for computation.

    A null document means no structured expansion facts, not proof that the door
    is fixed-size. Raw descriptions preserve syntax or qualifications that the
    structured fields cannot express. Source record claims are never official
    record results. Replace the whole immutable document when editing it.
    """

    schema_version: Literal[1] = 1
    raw_description: SourceText | None = None
    parameters: tuple[ExpansionParameter, ...] = Field(default=(), max_length=16)
    branches: tuple[ExpansionBranch, ...] = Field(default=(), max_length=64)
    evidence: tuple[ExpansionEvidence, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            msg = "Expansion parameter names must be unique."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        return self

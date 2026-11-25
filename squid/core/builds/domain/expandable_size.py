"""Single-axis opening sizes represented by the existing expansion document."""

from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionFact, ExpansionParameter

type OpeningAxis = Literal["width", "height", "depth"]
type MaximumKind = Literal["unknown", "finite", "unbounded"]

_AXES: tuple[OpeningAxis, ...] = ("width", "height", "depth")
_PARAMETER = "n"
_INVALID_VALUE = "value_error"


class ExpandableSize(ExpansionFact):
    """One adjustable dimension of the opening, independent of the measured sample."""

    axis: OpeningAxis
    minimum: int = Field(strict=True, ge=1)
    maximum_kind: MaximumKind = "unknown"
    maximum: int | None = Field(default=None, strict=True, ge=1)
    step: int = Field(default=1, strict=True, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if (self.maximum_kind == "finite") != (self.maximum is not None):
            msg = "Enter a maximum only for a finite range."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        if self.maximum is not None and (self.maximum < self.minimum or (self.maximum - self.minimum) % self.step):
            msg = "The maximum must be reachable from the minimum using the step."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        return self

    def to_expansion(self) -> DoorExpansion:
        formulas = {"opening_width": None, "opening_height": None, "opening_depth": None}
        formulas[f"opening_{self.axis}"] = _PARAMETER
        return DoorExpansion(
            parameters=(
                ExpansionParameter(
                    name=_PARAMETER,
                    minimum=self.minimum,
                    maximum_kind=self.maximum_kind,
                    maximum=self.maximum,
                    initial_steps=(),
                    repeating_steps=(self.step,),
                ),
            ),
            branches=(ExpansionBranch.model_validate(formulas),),
        )

    @classmethod
    def from_expansion(cls, expansion: DoorExpansion | None) -> Self | None:
        """Recognize a simple size only when it round-trips without losing facts."""
        if expansion is None or len(expansion.parameters) != 1 or len(expansion.branches) != 1:
            return None
        parameter, branch = expansion.parameters[0], expansion.branches[0]
        if not parameter.minimum or parameter.repeating_steps is None or len(parameter.repeating_steps) != 1:
            return None
        axis = next((axis for axis in _AXES if getattr(branch, f"opening_{axis}") == _PARAMETER), None)
        if axis is None:
            return None
        if parameter.maximum is not None and (parameter.maximum - parameter.minimum) % parameter.repeating_steps[0]:
            return None
        size = cls(
            axis=axis,
            minimum=parameter.minimum,
            maximum_kind=parameter.maximum_kind,
            maximum=parameter.maximum,
            step=parameter.repeating_steps[0],
        )
        return size if size.to_expansion() == expansion else None

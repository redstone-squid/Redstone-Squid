"""Immutable UI values with variance preserved during static analysis."""

from dataclasses import dataclass, field
from typing import dataclass_transform


# BasedPyright's synthesized __replace__ makes every frozen type parameter invariant.
# Calls to dataclasses.replace retain their normal typed contract.
@dataclass_transform(
    frozen_default=True,
    field_specifiers=(field,),
    skip_replace=True,  # pyrefly: ignore[unexpected-keyword]  # BasedPyright extension.
)
def immutable[T](cls: type[T]) -> type[T]:
    return dataclass(frozen=True, slots=True)(cls)

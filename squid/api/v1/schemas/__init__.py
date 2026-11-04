"""Version 1 REST transfer objects."""

from abc import abstractmethod
from typing import Self

from pydantic import BaseModel


class FromDomain[DomainT](BaseModel):
    """Base for response models with a named conversion from a domain value.

    The positional-only argument lets implementations use a descriptive parameter
    name. Conversions that need caller identity or other context can declare their
    own constructors instead of inheriting this base.
    """

    @classmethod
    @abstractmethod
    def from_domain(cls, value: DomainT, /) -> Self:
        """Build this representation from its domain value."""


__all__ = ["FromDomain"]

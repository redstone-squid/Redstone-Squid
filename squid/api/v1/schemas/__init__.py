"""Version 1 REST transfer objects."""

from abc import abstractmethod
from typing import Self

from pydantic import BaseModel


class FromDomain[DomainT](BaseModel):
    """A representation built from exactly one domain value.

    Implement it only where the mapping is total (every domain value has a representation) and
    context-free (the domain value is the only input). Anything needing request context, such as
    `VoteSessionDetail.from_domain(session, caller_account_id=...)`, takes an explicit constructor
    instead. The parameter is positional-only so implementations can name it after what they map.
    """

    @classmethod
    @abstractmethod
    def from_domain(cls, value: DomainT, /) -> Self:
        """Build this representation from its domain value."""


__all__ = ["FromDomain"]

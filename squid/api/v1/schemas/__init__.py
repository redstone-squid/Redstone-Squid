"""Version 1 REST transfer objects."""

from abc import abstractmethod
from typing import Self

from pydantic import BaseModel


class FromDomain[DomainT](BaseModel):
    """A representation built from exactly one domain value.

    Deliberately not a universal mixin. It applies where the mapping is *total*
    (every domain value has a representation) and *context-free* (the domain
    value is the only input). Where either fails, an explicit constructor is the
    honest signature: `VoteSessionDetail.from_domain(session,
    caller_account_id=...)` hides a ballot behind the caller's identity, and a
    mandatory base would have to either widen to `**kwargs` -- giving up the
    guarantee it exists for -- or push the request context into the domain.

    The parameter is positional-only so implementations can name it after what
    they map, rather than repeating `value`.
    """

    @classmethod
    @abstractmethod
    def from_domain(cls, value: DomainT, /) -> Self:
        """Build this representation from its domain value."""


__all__ = ["FromDomain"]

"""Stable screen identities; opening identity and permissions come from the request."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

type AccountTab = Literal["overview", "identity", "claims", "merge", "review"]


@dataclass(frozen=True, slots=True)
class Destination:
    """A serializable destination, separate from a live screen instance."""


@dataclass(frozen=True, slots=True)
class BuildIndex(Destination):
    pass


@dataclass(frozen=True, slots=True)
class BuildDetail(Destination):
    build_id: int


@dataclass(frozen=True, slots=True)
class DraftIndex(Destination):
    inbox: bool = False


@dataclass(frozen=True, slots=True)
class DraftDetail(Destination):
    draft_id: UUID


@dataclass(frozen=True, slots=True)
class AccountDestination(Destination):
    tab: AccountTab | None = None

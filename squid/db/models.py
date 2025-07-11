"""Pure domain models (as opposed to all the impure god objects scattering around in the codebase)."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, final, TypedDict

from squid.db.schema import VoteSessionResultLiteral


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Represents a custom event in the database."""

    id: int
    aggregate: str
    aggregate_id: int
    type: str
    payload: Mapping[str, Any]


class VoteSessionClosedPayload(TypedDict):
    """Payload for the VoteSessionClosed event."""

    result: VoteSessionResultLiteral
    closed_at: datetime


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class VoteSessionClosed(Event):
    """Represents a vote session closed event."""

    aggregate: str = "vote_session"
    type: str = "vote_session_closed"
    payload: VoteSessionClosedPayload

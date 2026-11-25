"""The source revision retained by a draft editing a published build."""

from dataclasses import dataclass

from squid.core.errors import JSONValue


@dataclass(frozen=True, slots=True)
class DraftEditResult:
    build_id: int
    revision: int
    review_vote_id: int | None


@dataclass(frozen=True, slots=True)
class DraftEditSource:
    build_id: int
    revision: int
    answers: dict[str, JSONValue]
    result: DraftEditResult | None = None

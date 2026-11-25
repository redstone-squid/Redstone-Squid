"""The source revision retained by a draft editing a published build."""

from pydantic import BaseModel, JsonValue


class DraftEditResult(BaseModel):
    build_id: int
    revision: int
    review_vote_id: int | None


class DraftEditSource(BaseModel):
    build_id: int
    revision: int
    answers: dict[str, JsonValue]
    result: DraftEditResult | None = None

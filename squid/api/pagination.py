"""Shared REST collection response types."""

from pydantic import BaseModel, ConfigDict


class Page[ItemT](BaseModel):
    """One cursor-addressable page of resource summaries."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    next_cursor: str | None
    has_more: bool

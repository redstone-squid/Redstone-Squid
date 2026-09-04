"""A complete outgoing Slack message payload."""

from dataclasses import dataclass
from typing import TypedDict

from slack_sdk.models.blocks import Block


class MessagePayloadKwargs(TypedDict):
    """Keyword arguments emitted for a Slack message client method."""

    text: str
    blocks: list[Block]


@dataclass(frozen=True, slots=True)
class MessagePayload:
    """Screen-reader text and SDK blocks for one Slack message."""

    text: str
    blocks: tuple[Block, ...] = ()

    def to_kwargs(self) -> MessagePayloadKwargs:
        """Return keyword arguments accepted by Slack message client methods."""
        return {"text": self.text, "blocks": list(self.blocks)}


__all__ = ["MessagePayload", "MessagePayloadKwargs"]

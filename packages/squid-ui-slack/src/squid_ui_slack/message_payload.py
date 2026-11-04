"""Text and blocks for an outgoing Slack message."""

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
        """Return message content kwargs; the caller supplies the channel and other delivery options."""
        return {"text": self.text, "blocks": list(self.blocks)}


__all__ = ["MessagePayload", "MessagePayloadKwargs"]

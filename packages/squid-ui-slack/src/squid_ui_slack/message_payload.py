"""A complete outgoing Slack message payload."""

from dataclasses import dataclass

from slack_sdk.models.blocks import Block


@dataclass(frozen=True, slots=True)
class MessagePayload:
    """Screen-reader text and SDK blocks for one Slack message."""

    text: str
    blocks: tuple[Block, ...] = ()

    def to_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by Slack message client methods."""
        return {"text": self.text, "blocks": list(self.blocks)}


__all__ = ["MessagePayload"]

"""A complete outgoing Slack message payload."""

from dataclasses import dataclass

from slack_sdk.models.blocks import Block


@dataclass(frozen=True, slots=True)
class MessagePayload:
    """What `MessageRenderer.draw` returns: fallback text plus SDK blocks the SDK has already accepted."""

    text: str
    """Shown in notifications and by screen readers when `blocks` is non-empty; the whole message otherwise."""
    blocks: tuple[Block, ...] = ()

    def to_kwargs(self) -> dict[str, object]:
        """Return `text` and `blocks` (as a list) for `chat_postMessage`, `chat_update` and `chat_postEphemeral`."""
        return {"text": self.text, "blocks": list(self.blocks)}


__all__ = ["MessagePayload"]

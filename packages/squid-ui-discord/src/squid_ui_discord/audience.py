"""Discord response audience values shared by legacy and facade delivery."""

from dataclasses import dataclass
from typing import Literal

from squid_ui.text import Text, TextLike


@dataclass(frozen=True, slots=True)
class Private:
    """Require an ephemeral interaction response or private-message delivery.

    For guild messages without an interaction, the payload goes to the actor's DMs and
    ``reason`` accompanies a public delivery confirmation. The ``"personal"`` audience
    alone does not provide this DM fallback.
    """

    reason: TextLike

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", Text(self.reason))


type Visibility = Literal["public", "personal"] | Private
type Audience = Visibility


__all__ = ["Audience", "Private", "Visibility"]

"""Discord response audience values shared by legacy and facade delivery."""

from dataclasses import dataclass
from typing import Literal

from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class Private:
    """Deliver where a guild channel can never see the payload."""

    reason: TextLike


type Visibility = Literal["public", "personal"] | Private
type Audience = Visibility


__all__ = ["Audience", "Private", "Visibility"]

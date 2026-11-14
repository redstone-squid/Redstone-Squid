"""Named native embed slots retain their individual overflow policies."""

from dataclasses import dataclass
from typing import Literal

from squid_ui.primitives.nodes import Text


@dataclass(frozen=True, slots=True)
class CardSlot:
    kind: Literal["title", "field_name", "field_value", "footer", "author"]
    text: Text

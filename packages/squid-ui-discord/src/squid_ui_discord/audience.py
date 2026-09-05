"""Who may see a Discord response.

`"public"` is a normal reply. `"personal"` is ephemeral where the source allows it (an
interaction, or a hybrid command invoked as a slash command) and a normal reply otherwise.
`Private` never falls back to the channel.
"""

from dataclasses import dataclass
from typing import Literal

from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class Private:
    """Deliver where a guild channel can never see the payload.

    Ephemeral where the source allows it, a plain reply when already in a direct message, and
    otherwise a direct message plus a public confirmation in the channel that quotes `reason`.
    Closed direct messages abandon the delivery with the chrome's `dm_unavailable` notice.
    """

    reason: TextLike
    """Why the reply is private, shown in the channel when the payload went to a direct message."""


type Visibility = Literal["public", "personal"] | Private
type Audience = Visibility


__all__ = ["Audience", "Private", "Visibility"]

"""Bot authorization domain values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class GlobalAdministrator:
    """A Discord user granted bot-wide administrative access by the owner."""

    discord_id: int
    granted_by_discord_id: int
    granted_at: Instant

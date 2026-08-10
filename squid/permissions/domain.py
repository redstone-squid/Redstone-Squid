"""Bot authorization domain values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class GlobalAdministrator:
    """An account granted application-wide administrative access."""

    account_id: int
    granted_by_account_id: int
    granted_at: Instant

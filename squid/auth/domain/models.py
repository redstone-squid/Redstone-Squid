"""Authentication credential domain values."""

from dataclasses import dataclass

from whenever import Instant

from squid.permissions.domain import Pattern


@dataclass(frozen=True, slots=True)
class ApiKey:
    """A stored service credential without its recoverable secret."""

    id: int
    key_id: str
    secret_hash: bytes
    label: str
    scopes: frozenset[Pattern]
    """Permission patterns bounding what this credential may do.

    Parsed, not raw text: a pattern is validated once at the boundary that
    accepts it, so nothing downstream re-parses per request or discovers a typo
    by silently matching nothing.

    Stored as `ARRAY(Text)` rather than an enum column because the catalogue is
    open by construction -- a pattern granted today selects a node registered
    tomorrow (`squid.permissions.domain.matching`), so an enum would need a
    migration every time a node is added.
    """
    owner_account_id: int | None
    created_by_account_id: int | None
    created_at: Instant
    expires_at: Instant | None = None
    revoked_at: Instant | None = None
    last_used_at: Instant | None = None
    last_used_ip: str | None = None

    def is_active_at(self, instant: Instant) -> bool:
        """Return whether this credential may authenticate at *instant*."""
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > instant)


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    """A newly issued credential and its one-time plaintext token."""

    key: ApiKey
    token: str

"""Authentication credential domain values."""

from dataclasses import dataclass

from whenever import Instant


@dataclass(frozen=True, slots=True)
class ApiKey:
    """A stored service credential without its recoverable secret."""

    id: int
    key_id: str
    secret_hash: bytes
    label: str
    scopes: frozenset[str]
    owner_user_id: int | None
    created_by: int | None
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

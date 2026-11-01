"""Authentication credential domain values."""

from dataclasses import dataclass

from squid.auth.models.entities import ApiKey


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    """A newly issued credential and its one-time plaintext token."""

    key: ApiKey
    token: str

"""Values exchanged with an external authorization-code identity source."""

from dataclasses import dataclass

from squid.accounts.domain import IdentityProvider


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """A verified subject returned by one authorization-code exchange."""

    provider: IdentityProvider
    subject: str
    display_name: str | None = None

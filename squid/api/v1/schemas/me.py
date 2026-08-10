"""Authenticated self-account representations."""

from pydantic import BaseModel, ConfigDict

from squid.accounts.domain import Account, IdentityProvider


class UserMe(BaseModel):
    """The caller's own linked-account data."""

    model_config = ConfigDict(extra="forbid")

    id: int
    discord_id: int
    minecraft_uuid: str | None
    ign: str | None
    consent_version: str | None
    consent_pending: bool

    @classmethod
    def from_domain(cls, account: Account, *, consent_pending: bool) -> "UserMe":
        discord = account.identity(IdentityProvider.DISCORD)
        java = account.identity(IdentityProvider.JAVA)
        assert account.id is not None and discord is not None and discord.discord_id is not None
        return cls(
            id=account.id,
            discord_id=discord.discord_id,
            minecraft_uuid=java.subject if java is not None else None,
            ign=java.display_name if java is not None else None,
            consent_version=account.consent.version if account.consent is not None else None,
            consent_pending=consent_pending,
        )

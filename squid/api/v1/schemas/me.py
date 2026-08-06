"""Authenticated self-account representations."""

from pydantic import BaseModel, ConfigDict

from squid.users.domain import UserAccount


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
    def from_domain(cls, account: UserAccount, *, consent_pending: bool) -> "UserMe":
        assert account.id is not None and account.discord_id is not None
        return cls(
            id=account.id,
            discord_id=account.discord_id,
            minecraft_uuid=str(account.minecraft_uuid) if account.minecraft_uuid is not None else None,
            ign=account.ign,
            consent_version=account.consent.version if account.consent is not None else None,
            consent_pending=consent_pending,
        )

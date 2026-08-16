"""Authenticated self-account representations."""

from pydantic import BaseModel, ConfigDict

from squid.accounts.domain import Account, IdentityProvider, IdentityRefresh


class UserMe(BaseModel):
    """The caller's own linked-account data."""

    model_config = ConfigDict(extra="forbid")

    id: int
    discord_id: int | None
    """Absent for callers that authenticated without Discord, such as a CLI device."""
    minecraft_uuid: str | None
    ign: str | None
    consent_version: str | None
    consent_pending: bool

    @classmethod
    def from_domain(cls, account: Account, *, consent_pending: bool) -> UserMe:
        discord = account.identity(IdentityProvider.DISCORD)
        java = account.identity(IdentityProvider.JAVA)
        assert account.id is not None
        return cls(
            id=account.id,
            discord_id=None if discord is None else discord.discord_id,
            minecraft_uuid=java.subject if java is not None else None,
            ign=java.display_name if java is not None else None,
            consent_version=account.consent.version if account.consent is not None else None,
            consent_pending=consent_pending,
        )


class MinecraftIdentityRefresh(BaseModel):
    """What re-reading the linked Minecraft name changed."""

    model_config = ConfigDict(extra="forbid")

    minecraft_uuid: str
    ign: str
    previous_ign: str | None
    renamed: bool
    claimed_creator_name: str | None
    """The creator credit now attributed to the caller under the current name."""
    retained_creator_names: tuple[str, ...]
    """Credits kept under previously verified names. A rename does not retract them."""
    contested_creator_name: str | None
    """Set when the current name is credited to a different account and was not taken."""
    pending_claim_id: int | None
    """The staff review opened for a contested name."""

    @classmethod
    def from_domain(cls, refresh: IdentityRefresh) -> MinecraftIdentityRefresh:
        return cls(
            minecraft_uuid=str(refresh.java_uuid),
            ign=refresh.current_name,
            previous_ign=refresh.previous_name,
            renamed=refresh.renamed,
            claimed_creator_name=None if refresh.claimed_alias is None else refresh.claimed_alias.name,
            retained_creator_names=refresh.retained_alias_names,
            contested_creator_name=None if refresh.contested_alias is None else refresh.contested_alias.name,
            pending_claim_id=None if refresh.opened_claim is None else refresh.opened_claim.id,
        )

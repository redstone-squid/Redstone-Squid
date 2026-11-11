"""Trusted identity and permission context shared by application operations."""

from dataclasses import dataclass

from squid.core.errors import AuthenticationError, InvalidStateError


@dataclass(frozen=True, slots=True, kw_only=True)
class Actor:
    """An account or Discord user acting in an optional guild context.

    Adapters supply these facts, never request bodies. Anonymous actors are valid
    permission inputs; operations requiring identity check it at their boundary.
    Capabilities contain only the nodes requested by a feature, not a complete
    permission cache. Permission services resolve current authority independently.
    """

    account_id: int | None = None
    discord_user_id: int | None = None
    guild_id: int | None = None
    discord_role_ids: frozenset[int] = frozenset()
    capabilities: frozenset[str] = frozenset()
    is_bot_owner: bool = False
    discord_guild_admin: bool = False
    highest_permission_rank: int | None = None

    @property
    def key(self) -> str:
        """Stable audit/replay identity, preferring the linked account."""
        if self.account_id is not None:
            return f"account:{self.account_id}"
        if self.discord_user_id is not None:
            return f"discord:{self.discord_user_id}"
        message = "An edit requires an identified actor"
        raise ValueError(message)

    def require_account_id(self) -> int:
        if self.account_id is None:
            raise AuthenticationError
        return self.account_id

    def require_discord_user_id(self) -> int:
        if self.discord_user_id is None:
            raise AuthenticationError
        return self.discord_user_id

    def require_permission_rank(self) -> int:
        """Require administration to have resolved the actor's delegation rank."""
        if self.highest_permission_rank is None:
            message = "Permission administration requires a resolved role rank"
            raise InvalidStateError(message)
        return self.highest_permission_rank

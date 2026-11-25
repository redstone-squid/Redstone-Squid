"""Trusted identity and permission context shared by application operations."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4

from squid.core.errors import AuthenticationError, InvalidStateError

impersonation_use: ContextVar[UUID | None] = ContextVar("impersonation_use", default=None)
"""Authorized invocation whose application transactions are being audited."""


class ActorKind(StrEnum):
    ANONYMOUS = "anonymous"
    GUEST = "guest"
    USER = "user"
    SERVICE = "service"
    WORKER = "worker"


@dataclass(frozen=True, slots=True, kw_only=True)
class Actor:
    """An account or Discord user acting in an optional guild context.

    Adapters supply these facts, never request bodies. Anonymous actors are valid
    permission inputs; operations requiring identity check it at their boundary.
    Capabilities contain only the nodes requested by a feature, not a complete
    permission cache. Permission services resolve current authority independently.
    """

    guest_session_id: UUID | None = None
    service_id: str | None = None
    worker_name: str | None = None
    credential_id: str | None = None
    credential_scopes: frozenset[str] | None = None
    account_id: int | None = None
    discord_user_id: int | None = None
    guild_id: int | None = None
    discord_role_ids: frozenset[int] = frozenset()
    capabilities: frozenset[str] = frozenset()
    is_bot_owner: bool = False
    discord_guild_admin: bool = False
    highest_permission_rank: int | None = None

    audit_use_id: UUID | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        identities = (
            self.account_id is not None or self.discord_user_id is not None,
            self.guest_session_id is not None,
            self.service_id is not None,
            self.worker_name is not None,
        )
        if sum(identities) > 1:
            message = "An actor must have a single principal kind"
            raise ValueError(message)
        if self.worker_name == "" or self.service_id == "":
            message = "Named principals require a nonempty identifier"
            raise ValueError(message)

    @property
    def kind(self) -> ActorKind:
        if self.worker_name is not None:
            return ActorKind.WORKER
        if self.service_id is not None:
            return ActorKind.SERVICE
        if self.guest_session_id is not None:
            return ActorKind.GUEST
        if self.account_id is not None or self.discord_user_id is not None:
            return ActorKind.USER
        return ActorKind.ANONYMOUS

    @property
    def reference(self) -> str:
        """A nonsecret attribution label, including unidentified public traffic."""
        return "anonymous" if self.kind is ActorKind.ANONYMOUS else self.key

    @contextmanager
    def audit_scope(self) -> Iterator[None]:
        """Carry a callback's attribution into its transaction, including nested services."""
        with impersonation_use.set(self.audit_use_id or impersonation_use.get()):
            yield

    @property
    def key(self) -> str:
        """Stable audit/replay identity, preferring the linked account."""
        if self.account_id is not None:
            return f"account:{self.account_id}"
        if self.discord_user_id is not None:
            return f"discord:{self.discord_user_id}"
        if self.worker_name is not None:
            return f"worker:{self.worker_name}"
        if self.service_id is not None:
            return f"service:{self.service_id}"
        if self.guest_session_id is not None:
            return f"guest:{self.guest_session_id}"
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


@dataclass(frozen=True, slots=True)
class ActingContext:
    """Application identity and its authenticated operator for one invocation."""

    operator: Actor
    effective: Actor
    session_id: UUID | None = None
    invocation_id: UUID = field(default_factory=uuid4, compare=False)

    @classmethod
    def current(cls) -> ActingContext:
        context = current_acting_context.get()
        if context is None:
            message = "Application operation requires an acting context"
            raise InvalidStateError(message)
        return context

    @classmethod
    def direct(cls, actor: Actor) -> ActingContext:
        return cls(actor, actor)

    @classmethod
    def worker(cls, name: str) -> ActingContext:
        return cls.direct(Actor(worker_name=name))

    @contextmanager
    def scope(self) -> Iterator[None]:
        """Bind attribution to nested operations for the lifetime of this invocation."""
        with current_acting_context.set(self), impersonation_use.set(self.effective.audit_use_id):
            yield


current_acting_context: ContextVar[ActingContext | None] = ContextVar("current_acting_context", default=None)

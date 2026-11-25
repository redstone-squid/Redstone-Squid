"""Trusted identity and permission context shared by application operations."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import wraps
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from squid.core.errors import AuthenticationError, AuthorizationError, InvalidStateError

impersonation_use: ContextVar[UUID | None] = ContextVar("impersonation_use", default=None)
"""Authorized invocation whose application transactions are being audited."""


class ActorKind(StrEnum):
    ANONYMOUS = "anonymous"
    GUEST = "guest"
    USER = "user"
    SERVICE = "service"
    WORKER = "worker"


class _Principal(ABC):
    """Operations shared by every application principal."""

    __slots__ = ()

    @property
    def linked_account_id(self) -> int | None:
        return self.account_id if isinstance(self, UserActor) else None

    @property
    def credential_reference(self) -> str | None:
        return self.credential_id if isinstance(self, UserActor | ServiceActor) else None

    @property
    def credential_limit(self) -> frozenset[str] | None:
        return self.credential_scopes if isinstance(self, UserActor | ServiceActor) else None

    @property
    def audit_reference(self) -> UUID | None:
        return self.audit_use_id if isinstance(self, UserActor) else None

    def require_user(self) -> UserActor:
        if not isinstance(self, UserActor):
            raise AuthenticationError
        return self

    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    def reference(self) -> str:
        """Nonsecret label used to attribute an operation."""
        return self.key

    @contextmanager
    def audit_scope(self) -> Iterator[None]:
        with impersonation_use.set(self.audit_reference or impersonation_use.get()):
            yield

    def require_account_id(self) -> int:
        account_id = self.require_user().account_id
        if account_id is None:
            raise AuthenticationError
        return account_id

    def require_discord_user_id(self) -> int:
        discord_id = self.require_user().discord_user_id
        if discord_id is None:
            raise AuthenticationError
        return discord_id

    def require_permission_rank(self) -> int:
        rank = self.highest_permission_rank if isinstance(self, UserActor) else None
        if rank is None:
            message = "Permission administration requires a resolved role rank"
            raise InvalidStateError(message)
        return rank


@dataclass(frozen=True, slots=True, kw_only=True)
class AnonymousActor(_Principal):
    kind: ClassVar[Literal[ActorKind.ANONYMOUS]] = ActorKind.ANONYMOUS

    @property
    def key(self) -> str:
        message = "An edit requires an identified actor"
        raise ValueError(message)

    @property
    def reference(self) -> str:
        return "anonymous"


@dataclass(frozen=True, slots=True, kw_only=True)
class GuestActor(_Principal):
    kind: ClassVar[Literal[ActorKind.GUEST]] = ActorKind.GUEST
    guest_session_id: UUID

    @property
    def key(self) -> str:
        return f"guest:{self.guest_session_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class UserActor(_Principal):
    """Account or Discord identity with membership and credential facts from trusted adapters."""

    kind: ClassVar[Literal[ActorKind.USER]] = ActorKind.USER
    account_id: int | None = None
    discord_user_id: int | None = None
    guild_id: int | None = None
    discord_role_ids: frozenset[int] = frozenset()
    capabilities: frozenset[str] = frozenset()
    is_bot_owner: bool = False
    discord_guild_admin: bool = False
    highest_permission_rank: int | None = None
    credential_id: str | None = None
    credential_scopes: frozenset[str] | None = None
    audit_use_id: UUID | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.account_id is None and self.discord_user_id is None:
            message = "A user requires an account or Discord identity"
            raise ValueError(message)

    @property
    def key(self) -> str:
        if self.account_id is not None:
            return f"account:{self.account_id}"
        return f"discord:{self.discord_user_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceActor(_Principal):
    kind: ClassVar[Literal[ActorKind.SERVICE]] = ActorKind.SERVICE
    service_id: str
    credential_id: str | None = None
    credential_scopes: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.service_id:
            message = "A service requires a nonempty identifier"
            raise ValueError(message)

    @property
    def key(self) -> str:
        return f"service:{self.service_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerActor(_Principal):
    kind: ClassVar[Literal[ActorKind.WORKER]] = ActorKind.WORKER
    worker_name: str

    def __post_init__(self) -> None:
        if not self.worker_name:
            message = "A worker requires a nonempty name"
            raise ValueError(message)

    @property
    def key(self) -> str:
        return f"worker:{self.worker_name}"


Actor = AnonymousActor | GuestActor | UserActor | ServiceActor | WorkerActor


@dataclass(frozen=True, slots=True)
class ActingContext:
    """Application identity and its authenticated operator for one invocation."""

    operator: Actor
    effective: Actor
    session_id: UUID | None = None
    invocation_id: UUID = field(default_factory=uuid4, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operator, Actor) or not isinstance(self.effective, Actor):
            message = "An acting context requires an Actor for both operator and effective identity"
            raise InvalidStateError(message)

    @classmethod
    def current(cls) -> ActingContext:
        context = current_acting_context.get()
        if context is None:
            message = "Application operation requires an acting context"
            raise InvalidStateError(message)
        return context

    @classmethod
    def for_actor(cls, actor: Actor) -> ActingContext:
        """Carry the invocation through trusted enrichment of its effective principal."""
        current = current_acting_context.get()
        if current is None:
            return cls.direct(actor)
        return current.with_actor(actor)

    def with_actor(self, actor: Actor) -> ActingContext:
        """Attach verified guild or linked-account facts to this invocation."""
        effective = self.effective
        if isinstance(effective, UserActor) and isinstance(actor, UserActor):
            same_discord = effective.discord_user_id is not None and effective.discord_user_id == actor.discord_user_id
            if effective.reference != actor.reference and not (
                same_discord and (effective.account_id is None or actor.account_id is None)
            ):
                message = "Resolved identity differs from the current effective actor"
                raise InvalidStateError(message)
            resolved: Actor = replace(
                actor,
                account_id=effective.account_id if actor.account_id is None else actor.account_id,
                credential_scopes=effective.credential_scopes,
                credential_id=effective.credential_id,
                audit_use_id=effective.audit_use_id,
            )
        elif effective.reference == actor.reference and effective.kind is actor.kind:
            resolved = effective
        else:
            message = "Resolved identity differs from the current effective actor"
            raise InvalidStateError(message)
        operator = resolved if self.session_id is None and self.operator == effective else self.operator
        return replace(self, operator=operator, effective=resolved)

    def require_worker(self) -> str:
        if not isinstance(self.effective, WorkerActor):
            raise AuthorizationError
        return self.effective.worker_name

    @classmethod
    def direct(cls, actor: Actor) -> ActingContext:
        return cls(actor, actor)

    @classmethod
    def worker(cls, name: str) -> ActingContext:
        return cls.direct(WorkerActor(worker_name=name))

    @contextmanager
    def scope(self) -> Iterator[None]:
        """Bind attribution to nested operations for the lifetime of this invocation."""
        with current_acting_context.set(self), impersonation_use.set(self.effective.audit_reference):
            yield


current_acting_context: ContextVar[ActingContext | None] = ContextVar("current_acting_context", default=None)


def application_operation[**P, R](
    operation: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Bind an operation's required keyword-only context through nested calls and transactions."""

    @wraps(operation)
    async def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        context = kwargs.get("context")
        if not isinstance(context, ActingContext):
            message = "An application operation requires an acting context"
            raise InvalidStateError(message)
        with context.scope():
            return await operation(*args, **kwargs)

    return invoke

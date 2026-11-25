"""Authorize account impersonation before resolving the target's application actor."""

from dataclasses import dataclass
from uuid import UUID

from whenever import Instant

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor, application_operation
from squid.core.auth.application.ports import ImpersonationUnitOfWork
from squid.core.auth.models.sessions import ImpersonationSession
from squid.core.errors import AuthorizationError, NotFoundError, ValidationError
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import ACCOUNT_IMPERSONATE
from squid.core.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class AuthorizedImpersonation:
    target: Account
    discord_id: int | None
    use_id: UUID


class ImpersonationService:
    def __init__(self, factory: UnitOfWorkFactory[ImpersonationUnitOfWork], permissions: PermissionService):
        self.factory = factory
        self.permissions = permissions

    async def _authorize(self, operator: Actor, uow: ImpersonationUnitOfWork) -> None:
        # Guild roles and guild-scoped rules cannot confer installation-wide access.
        await self.permissions.require(
            Actor(
                account_id=operator.require_account_id(),
                is_bot_owner=operator.is_bot_owner,
                credential_id=operator.credential_id,
                credential_scopes=operator.credential_scopes,
            ),
            ACCOUNT_IMPERSONATE,
            uow=uow,
        )

    @application_operation
    async def start(
        self, binding: str, target_id: int, reason: str, *, context: ActingContext, target_discord_id: int | None = None
    ) -> ImpersonationSession:
        operator = context.operator
        reason = reason.strip()
        if not reason or len(reason) > 500:
            msg = "Give a debugging reason of at most 500 characters."
            raise ValidationError(msg)
        now = Instant.now()
        async with self.factory() as uow:
            await self._authorize(operator, uow)
            account = await uow.accounts.get_by_id(target_id)
            if account is None:
                msg = "Unknown target account."
                raise NotFoundError(msg)
            if (
                target_discord_id is not None
                and account.identity_for(IdentityProvider.DISCORD, str(target_discord_id)) is None
            ):
                raise AuthorizationError
            session = ImpersonationSession(
                operator_id=operator.require_account_id(),
                target_id=target_id,
                target_discord_id=target_discord_id,
                binding=binding,
                reason=reason,
                created_at=now,
                expires_at=now.add(minutes=15),
            )
            await uow.impersonation.add(session)
            await uow.commit()
        return session

    @application_operation
    async def use(
        self, binding: str, session_id: UUID, operation: str, *, context: ActingContext
    ) -> AuthorizedImpersonation:
        operator = context.operator
        async with self.factory() as uow:
            await self._authorize(operator, uow)
            session = await uow.impersonation.locked(session_id, operator.require_account_id(), binding)
            if session is None or session.revoked_at is not None or session.expires_at <= Instant.now():
                raise AuthorizationError
            account = await uow.accounts.get_by_id(session.target_id)
            if account is None:
                raise AuthorizationError
            if (
                session.target_discord_id is not None
                and account.identity_for(IdentityProvider.DISCORD, str(session.target_discord_id)) is None
            ):
                raise AuthorizationError
            use_id = await uow.impersonation.record_use(session.id, operation)
            await uow.commit()
        return AuthorizedImpersonation(account, session.target_discord_id, use_id)

    @application_operation
    async def inspect(self, binding: str, session_id: UUID, *, context: ActingContext) -> ImpersonationSession:
        operator = context.operator
        async with self.factory() as uow:
            session = await uow.impersonation.locked(session_id, operator.require_account_id(), binding)
            if session is None:
                raise NotFoundError
            return session

    @application_operation
    async def stop(self, binding: str, session_id: UUID, *, context: ActingContext) -> None:
        operator = context.operator
        async with self.factory() as uow:
            session = await uow.impersonation.locked(session_id, operator.require_account_id(), binding)
            if session is not None and session.revoked_at is None:
                session.revoked_at = Instant.now()
                await uow.commit()

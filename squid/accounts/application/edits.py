"""Profile and visibility edits share the owning account's optimistic revision."""

from dataclasses import dataclass, fields
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from squid.accounts.application.ports import AccountRepository, AccountUnitOfWork
from squid.accounts.domain import ProfileLink, ProfileUpdate
from squid.accounts.errors import AccountIdentityNotFoundError, ConsentRequiredError
from squid.accounts.models import Account, AccountProfile
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ValidationError
from squid.core.sentinel import UNSET
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.edits.fields import Aggregate, FieldCommand
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import ACCOUNT_PROFILE_MODERATE, ACCOUNT_SELF_MANAGE

PROFILE_CLEAR = "accounts.profile_clear"
PROFILE_EDIT = "accounts.profile"
VISIBILITY_EDIT = "accounts.visibility"
UNLINK_EDIT = "accounts.unlink"


def account_target(account_id: int) -> EditTarget:
    return EditTarget("account", str(account_id))


class ProfileValues(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    display_name: str | None = None
    bio: str | None = None
    pronouns: str | None = None
    links: tuple[ProfileLink, ...] = ()
    hidden: bool = False
    avatar_identity_id: int | None = None

    def update(self) -> ProfileUpdate:
        return ProfileUpdate(**{name: getattr(self, name) for name in type(self).model_fields})

    @classmethod
    def patch(cls, update: ProfileUpdate) -> EditValues:
        values = cls.model_validate(update.validated().apply(AccountProfile.empty(0))).model_dump(mode="json")
        return EditValues.of(
            {field.name: values[field.name] for field in fields(update) if getattr(update, field.name) is not UNSET}
        )


@dataclass(frozen=True, slots=True)
class AccountEditorSnapshot:
    account: Account
    profile: AccountProfile


class AccountEditStore(AccountRepository, Protocol):
    async def record_profile_clear(
        self, account_id: int, actor_account_id: int, operation_id: UUID, revision: int
    ) -> None: ...

    async def has_current_consent(self, account_id: int) -> bool: ...

    async def lock_for_edit(self, account_id: int) -> Account: ...

    async def flush_edit_revision(self, account: Account, expected: Revision) -> Revision: ...


class AccountEditUnitOfWork(AccountUnitOfWork, EditUnitOfWork, PermissionUnitOfWork, Protocol):
    @property
    def accounts(self) -> AccountEditStore: ...


class AccountEdits:
    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

        async def load(uow: AccountEditUnitOfWork, command: EditCommand) -> Account:
            return await uow.accounts.lock_for_edit(int(command.expected[0].target.key))

        async def flush(uow: AccountEditUnitOfWork, account: Account, expected: Revision) -> Revision:
            return await uow.accounts.flush_edit_revision(account, expected)

        self.aggregate = Aggregate("account", load, lambda account: account.revision, flush)

    async def authorize(self, uow: AccountEditUnitOfWork, actor: Actor, account: Account) -> None:
        if actor.account_id != account.id or not await self._permissions.allows_in(uow, actor, ACCOUNT_SELF_MANAGE):
            raise AuthorizationError
        if not await uow.accounts.has_current_consent(account.id):
            raise ConsentRequiredError(account_id=account.id)


class AccountProfileEdit(FieldCommand[AccountEditUnitOfWork, Account, ProfileValues]):
    def __init__(self, permissions: PermissionService) -> None:
        policy = AccountEdits(permissions)
        super().__init__(policy.aggregate, ProfileValues, policy.authorize)

    async def capture(self, uow: AccountEditUnitOfWork, root: Account, command: EditCommand) -> ProfileValues:
        profile = await uow.accounts.get_profile(root.id)
        assert profile is not None
        return ProfileValues.model_validate(profile)

    async def validate(
        self,
        uow: AccountEditUnitOfWork,
        actor: Actor,
        root: Account,
        command: EditCommand,
        before: ProfileValues,
        after: ProfileValues,
    ) -> ProfileValues:
        return ProfileValues.model_validate(ProfileValues.patch(after.update().validated()).unpack())

    async def write(
        self, uow: AccountEditUnitOfWork, root: Account, before: ProfileValues, after: ProfileValues
    ) -> None:
        await uow.accounts.upsert_profile(root.id, after.update())


class VisibilityValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    identity_id: int
    is_public: bool


class AccountVisibilityEdit(FieldCommand[AccountEditUnitOfWork, Account, VisibilityValues]):
    def __init__(self, permissions: PermissionService) -> None:
        policy = AccountEdits(permissions)
        super().__init__(policy.aggregate, VisibilityValues, policy.authorize)

    async def capture(self, uow: AccountEditUnitOfWork, root: Account, command: EditCommand) -> VisibilityValues:
        identity_id = VisibilityValues.model_validate(command.values.unpack()).identity_id
        identity = next((row for row in root.identities if row.id == identity_id), None)
        if identity is None:
            raise AccountIdentityNotFoundError(identity_id, account_id=root.id)
        return VisibilityValues(identity_id=identity_id, is_public=identity.is_public)

    async def write(
        self, uow: AccountEditUnitOfWork, root: Account, before: VisibilityValues, after: VisibilityValues
    ) -> None:
        await uow.accounts.set_identity_visibility(root.id, after.identity_id, is_public=after.is_public)


class AccountUnlinkEdit(AccountEdits):
    async def prepare(self, uow: AccountEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedEdit:
        account = await self.aggregate.lock(uow, command)
        await self.authorize(uow, actor, account)
        identity_id = command.values.unpack().get("identity_id")
        if not isinstance(identity_id, int) or isinstance(identity_id, bool):
            message = "An unlink requires an identity ID"
            raise ValidationError(message)
        if not any(identity.id == identity_id for identity in account.identities):
            raise AccountIdentityNotFoundError(identity_id, account_id=account.id)
        return PreparedEdit(
            EditValues.of({"identity_id": identity_id, "present": True}),
            EditValues.of({"identity_id": identity_id, "present": False}),
            final=True,
            revisions=(Revision(command.expected[0].target, account.revision),),
        )

    async def apply(
        self, uow: AccountEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        account = await self.aggregate.lock(uow, command)
        identity_id = prepared.before.unpack()["identity_id"]
        assert isinstance(identity_id, int)
        await uow.accounts.unlink_identity(account.id, identity_id)
        return (await uow.accounts.flush_edit_revision(account, prepared.revisions[0]),)


class AccountProfileClear(AccountEdits):
    async def prepare(self, uow: AccountEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedEdit:
        if command.values.unpack() or command.purpose != "edit":
            raise ValidationError
        if actor.account_id is None or not await self._permissions.allows_in(uow, actor, ACCOUNT_PROFILE_MODERATE):
            raise AuthorizationError
        account = await self.aggregate.lock(uow, command)
        # Even an empty profile records the staff action, without keeping erased content in a receipt.
        return PreparedEdit(
            EditValues.of({"cleared": False}),
            EditValues.of({"cleared": True}),
            final=True,
            revisions=(Revision(command.expected[0].target, account.revision),),
        )

    async def apply(
        self, uow: AccountEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        account = await self.aggregate.lock(uow, command)
        await uow.accounts.clear_profile(account.id)
        revision = await uow.accounts.flush_edit_revision(account, prepared.revisions[0])
        await uow.accounts.record_profile_clear(
            account.id, actor.require_account_id(), command.operation_id, revision.value
        )
        return (revision,)

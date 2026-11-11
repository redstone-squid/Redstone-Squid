"""Profile and visibility edits share the owning account's optimistic revision."""

from dataclasses import dataclass, fields
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from squid.accounts.application.ports import AccountRepository, AccountUnitOfWork
from squid.accounts.domain import ProfileLink, ProfileUpdate
from squid.accounts.errors import AccountIdentityNotFoundError, ConsentRequiredError
from squid.accounts.models import Account, AccountProfile
from squid.core.errors import AuthorizationError
from squid.core.sentinel import UNSET
from squid.edits.application import EditUnitOfWork, FieldEditHandler
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import ACCOUNT_SELF_MANAGE
from squid.persistence.revisions import require_revision

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
    async def has_current_consent(self, account_id: int) -> bool: ...

    async def lock_for_edit(self, account_id: int) -> Account: ...

    async def flush_edit_revision(self, account: Account, expected: Revision) -> Revision: ...


class AccountEditUnitOfWork(AccountUnitOfWork, EditUnitOfWork, PermissionUnitOfWork, Protocol):
    @property
    def accounts(self) -> AccountEditStore: ...


class AccountFieldEdit(FieldEditHandler[AccountEditUnitOfWork]):
    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    @staticmethod
    def account_id(command: EditCommand) -> int:
        if len(command.expected) != 1 or command.expected[0].target.kind != "account":
            message = "Account edits must guard the owning account revision"
            raise ValueError(message)
        return int(command.expected[0].target.key)

    async def account(self, uow: AccountEditUnitOfWork, command: EditCommand) -> Account:
        account = await uow.accounts.lock_for_edit(self.account_id(command))
        require_revision(command.expected[0], account.revision)
        return account

    async def authorize(self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand) -> None:
        account_id = self.account_id(command)
        if actor.account_id != account_id or not await self._permissions.allows_in(uow, actor, ACCOUNT_SELF_MANAGE):
            raise AuthorizationError
        if not await uow.accounts.has_current_consent(account_id):
            raise ConsentRequiredError(account_id=account_id)


class AccountProfileEdit(AccountFieldEdit):
    fields = frozenset(ProfileValues.model_fields)

    async def load(self, uow: AccountEditUnitOfWork, command: EditCommand) -> EditValues:
        account = await self.account(uow, command)
        profile = await uow.accounts.get_profile(account.id)
        assert profile is not None
        return EditValues.of(ProfileValues.model_validate(profile).model_dump(mode="json"))

    async def validate(
        self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand, values: EditValues
    ) -> EditValues:
        await self.authorize(uow, actor, command)
        normalized = ProfileValues.model_validate(values.unpack()).update().validated()
        return ProfileValues.patch(normalized)

    async def apply(
        self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        account = await self.account(uow, command)
        await uow.accounts.upsert_profile(account.id, ProfileValues.model_validate(prepared.after.unpack()).update())
        return (await uow.accounts.flush_edit_revision(account, command.expected[0]),)


class VisibilityValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    identity_id: int
    is_public: bool


class AccountVisibilityEdit(AccountFieldEdit):
    fields = frozenset(VisibilityValues.model_fields)

    async def load(self, uow: AccountEditUnitOfWork, command: EditCommand) -> EditValues:
        account = await self.account(uow, command)
        identity_id = VisibilityValues.model_validate(command.values.unpack()).identity_id
        identity = next((row for row in account.identities if row.id == identity_id), None)
        if identity is None:
            raise AccountIdentityNotFoundError(identity_id, account_id=account.id)
        return EditValues.of({"identity_id": identity_id, "is_public": identity.is_public})

    async def validate(
        self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand, values: EditValues
    ) -> EditValues:
        await self.authorize(uow, actor, command)
        return EditValues.of(VisibilityValues.model_validate(values.unpack()).model_dump(mode="json"))

    async def apply(
        self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        account = await self.account(uow, command)
        values = VisibilityValues.model_validate(prepared.after.unpack())
        await uow.accounts.set_identity_visibility(account.id, values.identity_id, is_public=values.is_public)
        return (await uow.accounts.flush_edit_revision(account, command.expected[0]),)


class AccountUnlinkEdit(AccountFieldEdit):
    async def prepare(self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand) -> PreparedEdit:
        account = await self.account(uow, command)
        await self.authorize(uow, actor, command)
        identity_id = command.values.unpack().get("identity_id")
        if not isinstance(identity_id, int) or isinstance(identity_id, bool):
            message = "An unlink requires an identity ID"
            raise TypeError(message)
        if not any(identity.id == identity_id for identity in account.identities):
            raise AccountIdentityNotFoundError(identity_id, account_id=account.id)
        return PreparedEdit(
            EditValues.of({"identity_id": identity_id, "present": True}),
            EditValues.of({"identity_id": identity_id, "present": False}),
            final=True,
        )

    async def apply(
        self, uow: AccountEditUnitOfWork, actor: Subject, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        account = await self.account(uow, command)
        identity_id = prepared.before.unpack()["identity_id"]
        assert isinstance(identity_id, int)
        await uow.accounts.unlink_identity(account.id, identity_id)
        return (await uow.accounts.flush_edit_revision(account, command.expected[0]),)

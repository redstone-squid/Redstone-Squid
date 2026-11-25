"""A transport recorder for account edit commands; database invariants have integration tests."""

from dataclasses import fields
from typing import Protocol

from squid.core.accounts.application.edits import UNLINK_EDIT, ProfileValues
from squid.core.accounts.domain import ProfileUpdate
from squid.core.accounts.models import AccountIdentity, AccountProfile
from squid.core.actors import ActingContext
from squid.core.edits.domain import EditCommand, EditOutcome, EditReceipt, EditResult, EditValues, Revision


class AccountStore(Protocol):
    """Where recorded edits land: the in-memory repository, or a test's own recorder."""

    async def upsert_profile(self, account_id: int, update_request: ProfileUpdate) -> AccountProfile: ...
    async def set_identity_visibility(
        self, account_id: int, identity_id: int, *, is_public: bool
    ) -> AccountIdentity: ...
    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None: ...


class AccountEditRecorder:
    def __init__(self, accounts: AccountStore) -> None:
        self.accounts = accounts
        self.commands: list[EditCommand] = []

    async def execute(self, command: EditCommand, *, context: ActingContext) -> EditResult:
        actor = context.effective
        self.commands.append(command)
        account_id = int(command.expected[0].target.key)
        values = command.values.unpack()
        before = EditValues.of({})
        match command.kind:
            case "accounts.profile":
                normalized = ProfileValues.model_validate(values)
                update = ProfileUpdate(
                    **{
                        field.name: getattr(normalized, field.name)
                        for field in fields(ProfileUpdate)
                        if field.name in values
                    }
                )
                await self.accounts.upsert_profile(account_id, update)
                before = EditValues.of(
                    ProfileValues.model_validate(AccountProfile.empty(account_id)).model_dump(mode="json")
                )
            case "accounts.visibility":
                assert isinstance(values["identity_id"], int)
                assert isinstance(values["is_public"], bool)
                await self.accounts.set_identity_visibility(
                    account_id, values["identity_id"], is_public=values["is_public"]
                )
                before = EditValues.of(values | {"is_public": not values["is_public"]})
            case "accounts.unlink":
                assert isinstance(values["identity_id"], int)
                await self.accounts.unlink_identity(account_id, values["identity_id"])
            case _:
                raise AssertionError(command.kind)
        revisions = tuple(
            Revision(version.target, (version.value if version.value is not None else 1) + 1)
            for version in command.expected
        )
        receipt = EditReceipt(command, before, command.values, revisions, final=command.kind == UNLINK_EDIT)
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, revisions, changed=True, final=receipt.final
            ),
            receipt,
        )

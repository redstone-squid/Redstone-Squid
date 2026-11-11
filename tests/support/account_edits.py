"""A transport recorder for account edit commands; database invariants have integration tests."""

from dataclasses import fields

from squid.accounts.application import AccountService
from squid.accounts.application.edits import (
    UNLINK_EDIT,
    AccountEditorSnapshot,
    ProfileValues,
)
from squid.accounts.domain import ProfileUpdate
from squid.accounts.errors import AccountNotFoundError
from squid.accounts.models import AccountProfile
from squid.core.actors import Actor
from squid.edits.domain import EditCommand, EditOutcome, EditReceipt, EditResult, EditValues, Revision


class SnapshotAccountService(AccountService):
    async def editor_snapshot(self, account_id: int) -> AccountEditorSnapshot:
        account = await self.get_account_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return AccountEditorSnapshot(account, await self.get_profile(account_id))


class AccountEditRecorder:
    def __init__(self, accounts: AccountService) -> None:
        self.accounts = accounts
        self.commands: list[EditCommand] = []

    async def execute(self, command: EditCommand, actor: Actor) -> EditResult:
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
                await self.accounts.update_profile(account_id, update)
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

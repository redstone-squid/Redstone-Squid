"""Profile receipts fence identity changes, consent, and final unlinking."""

from functools import partial

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.application.edits import (
    PROFILE_EDIT,
    UNLINK_EDIT,
    VISIBILITY_EDIT,
    AccountProfileEdit,
    AccountUnlinkEdit,
    AccountVisibilityEdit,
    account_target,
)
from squid.accounts.domain import AccountConsent, ProfileUpdate
from squid.accounts.errors import ConsentRequiredError, LastIdentityError
from squid.accounts.models import AccountIdentity
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, EditResult, EditValues, Revision, RevisionConflictError
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork


async def test_profile_and_identity_compete_on_one_revision_and_undo_checks_current_consent(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    permissions = PermissionService(uow_factory=factory)
    executor = EditExecutor(
        factory, {PROFILE_EDIT: AccountProfileEdit(permissions), VISIBILITY_EDIT: AccountVisibilityEdit(permissions)}
    )
    async with factory() as uow:
        account = await uow.accounts.create(
            consent=AccountConsent.grant_current(), identities=(AccountIdentity.discord(999),)
        )
        await uow.commit()
    actor = Actor(account_id=account.id)
    expected = (Revision(account_target(account.id), account.revision),)
    outcomes: list[EditResult | RevisionConflictError] = []

    async def run(kind: str, values: EditValues) -> None:
        try:
            outcomes.append(await executor.execute(EditCommand(kind, expected, values), actor))
        except RevisionConflictError as error:
            outcomes.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run, PROFILE_EDIT, EditValues.of({"display_name": "  Builder  "}))
        tasks.start_soon(
            run, VISIBILITY_EDIT, EditValues.of({"identity_id": account.identities[0].id, "is_public": False})
        )
    saved = next(result for result in outcomes if isinstance(result, EditResult))
    assert sum(isinstance(result, RevisionConflictError) for result in outcomes) == 1
    assert saved.receipt is not None
    if saved.receipt.command.kind == PROFILE_EDIT:
        assert saved.receipt.after.unpack()["display_name"] == "Builder"
    with pytest.raises(AuthorizationError):
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), Actor(account_id=account.id + 1))
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    assert undone.outcome.revisions[0].value > saved.outcome.revisions[0].value
    # A provider update is a competing writer even when it changes no profile text.
    async with factory() as uow:
        await uow.accounts.set_identity_avatar_key(account.id, account.identities[0].id, "changed-avatar")
        await uow.commit()
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.redo(undone.outcome.revisions), actor)
    async with factory() as uow:
        current = await uow.accounts.lock_for_edit(account.id)
        current.consent_version = None
        current.consented_at = None
        await uow.commit()
    with pytest.raises(ConsentRequiredError):
        await executor.execute(
            EditCommand(
                PROFILE_EDIT, (Revision(account_target(account.id), current.revision),), EditValues.of({"bio": "New"})
            ),
            actor,
        )


async def test_final_unlink_retains_profile_and_last_identity_guard(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    executor = EditExecutor(factory, {UNLINK_EDIT: AccountUnlinkEdit(PermissionService(uow_factory=factory))})
    async with factory() as uow:
        account = await uow.accounts.create(
            consent=AccountConsent.grant_current(),
            identities=(AccountIdentity.discord(999), AccountIdentity.discord(1000)),
        )
        identity_id = account.identities[0].id
        assert identity_id is not None
        await uow.accounts.upsert_profile(account.id, ProfileUpdate(bio="Keep", avatar_identity_id=identity_id))
        await uow.commit()
    actor = Actor(account_id=account.id)
    result = await executor.execute(
        EditCommand(
            UNLINK_EDIT,
            (Revision(account_target(account.id), account.revision),),
            EditValues.of({"identity_id": identity_id}),
        ),
        actor,
    )
    assert result.receipt is not None
    assert result.receipt.final
    async with factory() as uow:
        profile = await uow.accounts.get_profile(account.id)
        assert profile is not None
        assert profile.bio == "Keep"
        assert profile.avatar_identity_id is None
    with pytest.raises(LastIdentityError):
        await executor.execute(
            EditCommand(
                UNLINK_EDIT, result.outcome.revisions, EditValues.of({"identity_id": account.identities[1].id})
            ),
            actor,
        )

"""Notification aggregates reject stale writers without coupling unrelated inbox items."""

from functools import partial

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import AccountConsent
from squid.edits.application import EditExecutor
from squid.edits.domain import EditActor, EditCommand, EditResult, EditValues, Revision, RevisionConflictError
from squid.events.models import DomainEvent
from squid.notifications.application import NotificationService
from squid.notifications.domain import (
    InboxVisibility,
    NotificationKind,
    NotificationSource,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.edits import (
    INBOX_EDIT,
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    InboxReadEdit,
    NotificationPreferencesEdit,
    NotificationSubscriptionsEdit,
    SubscriptionsValues,
    inbox_target,
    notification_target,
)
from squid.notifications.errors import NotificationNotFoundError
from squid.notifications.models import InboxNotification, NotificationSubscription
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Subject
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork


async def test_preferences_and_follows_compete_on_one_account_revision(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    permissions = PermissionService(uow_factory=factory)
    executor = EditExecutor(
        factory,
        {
            PREFERENCES_EDIT: NotificationPreferencesEdit(permissions),
            SUBSCRIPTIONS_EDIT: NotificationSubscriptionsEdit(permissions),
        },
    )
    service = NotificationService(uow_factory=factory)
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.commit()
    actor = EditActor(Subject(account_id=account.id))
    expected = (Revision(notification_target(account.id), 0),)
    results: list[EditResult | RevisionConflictError] = []

    async def edit(kind: str, values: EditValues) -> None:
        try:
            results.append(await executor.execute(EditCommand(kind, expected, values), actor))
        except RevisionConflictError as error:
            results.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(edit, PREFERENCES_EDIT, EditValues.of({"web_enabled": True}))
        tasks.start_soon(
            edit,
            SUBSCRIPTIONS_EDIT,
            EditValues.of({"create": {"kind": "record_filter", "record_filter": {"build_kinds": ["door"]}}}),
        )
    assert sum(isinstance(result, RevisionConflictError) for result in results) == 1
    saved = next(result for result in results if isinstance(result, EditResult))
    assert saved.receipt is not None
    assert (await service.preferences(account.id)).revision == 1
    undone = await executor.execute(saved.receipt.inverse(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    assert undone.outcome.revisions[0].value == 2
    await service.subscribe(
        account.id,
        kind=SubscriptionKind.RECORD_FILTER,
        record_filter=RecordSubscriptionFilter(build_kinds=frozenset({"utility"})),
    )
    with pytest.raises(RevisionConflictError):
        await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)


async def test_follow_creation_undo_and_redo_keep_identity_and_monotonic_revision(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    executor = EditExecutor(
        factory, {SUBSCRIPTIONS_EDIT: NotificationSubscriptionsEdit(PermissionService(uow_factory=factory))}
    )
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.commit()
    actor = EditActor(Subject(account_id=account.id))
    created = await executor.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (Revision(notification_target(account.id), 0),),
            EditValues.of({"create": {"kind": "record_filter", "record_filter": {"build_kinds": ["door"]}}}),
        ),
        actor,
    )
    assert created.receipt is not None
    subscription = SubscriptionsValues.model_validate(created.receipt.after.unpack()).subscriptions[0]
    undone = await executor.execute(created.receipt.inverse(created.outcome.revisions), actor)
    assert undone.receipt is not None
    async with migrated_session_factory() as session:
        row = await session.get(NotificationSubscription, subscription.id)
        assert row is not None
        assert row.enabled is False
    redone = await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)
    assert redone.receipt is not None
    assert redone.receipt.after == created.receipt.after
    assert redone.outcome.revisions[0].value == 3
    repeated = await executor.execute(
        EditCommand(SUBSCRIPTIONS_EDIT, redone.outcome.revisions, created.receipt.command.values), actor
    )
    assert not repeated.outcome.changed
    assert repeated.outcome.revisions == redone.outcome.revisions
    async with migrated_session_factory() as session:
        assert len((await session.scalars(select(NotificationSubscription))).all()) == 1

    await executor.execute(redone.receipt.inverse(redone.outcome.revisions), actor)
    row, revision = await NotificationService(uow_factory=factory).subscription_snapshot(
        account.id,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=RecordSubscriptionFilter(build_kinds=frozenset({"door"})),
    )
    assert row is not None
    assert row.id == subscription.id
    assert not row.enabled
    assert revision == 4


async def test_inbox_batch_conflict_is_atomic_and_arrivals_do_not_block_undo(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    executor = EditExecutor(factory, {INBOX_EDIT: InboxReadEdit(PermissionService(uow_factory=factory))})
    service = NotificationService(uow_factory=factory)
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.notifications.update_preferences(account.id, web_enabled=True, dm_enabled=False)
        await uow.commit()
    actor = EditActor(Subject(account_id=account.id))

    async def arrive(kind: NotificationKind = NotificationKind.BUILD_CONFIRMED) -> InboxNotification:
        async with migrated_session_factory.begin() as session:
            event = DomainEvent(event_type="build.confirmed", aggregate_kind="build", aggregate_id=1, payload={})
            session.add(event)
            await session.flush()
            row = InboxNotification(
                account_id=account.id,
                event_id=event.id,
                source_key=NotificationSource(event.id, kind).key_for(account.id),
                kind=kind,
                payload={},
                web_visible=True,
            )
            session.add(row)
            await session.flush()
            return row

    first, second = await arrive(), await arrive()
    await service.mark_read(account.id, second.id, visibility=InboxVisibility())
    with pytest.raises(RevisionConflictError):
        await executor.execute(
            EditCommand(
                INBOX_EDIT,
                (Revision(inbox_target(first.id), 1), Revision(inbox_target(second.id), 1)),
                EditValues.of({"states": {str(first.id): True, str(second.id): True}}),
            ),
            actor,
        )
    async with migrated_session_factory() as session:
        current = await session.get(InboxNotification, first.id)
        assert current is not None
        assert current.read_at is None
    saved = await executor.execute(
        EditCommand(
            INBOX_EDIT, (Revision(inbox_target(first.id), 1),), EditValues.of({"states": {str(first.id): True}})
        ),
        actor,
    )
    assert saved.receipt is not None
    await arrive()
    undone = await executor.execute(saved.receipt.inverse(saved.outcome.revisions), actor)
    assert undone.outcome.revisions == (Revision(inbox_target(first.id), 3),)

    staff = await arrive(NotificationKind.STAFF_BUILD_SUBMITTED)
    staff_saved = await executor.execute(
        EditCommand(
            INBOX_EDIT, (Revision(inbox_target(staff.id), 1),), EditValues.of({"states": {str(staff.id): True}})
        ),
        EditActor(Subject(account_id=account.id, is_bot_owner=True)),
    )
    assert staff_saved.receipt is not None
    with pytest.raises(NotificationNotFoundError):
        await executor.execute(staff_saved.receipt.inverse(staff_saved.outcome.revisions), actor)

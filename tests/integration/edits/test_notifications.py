"""Notification aggregates reject stale writers without coupling unrelated inbox items."""

from functools import partial

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import AccountConsent, ClaimMethod
from squid.core.accounts.errors import ConsentRequiredError
from squid.core.accounts.models import Account, CreatorAlias
from squid.core.actors import ActingContext, UserActor
from squid.core.edits.application import EditExecutor
from squid.core.edits.domain import EditCommand, EditResult, EditValues, Revision, RevisionConflictError
from squid.core.events.models import DomainEvent
from squid.core.notifications.application import NotificationService
from squid.core.notifications.domain import (
    NotificationKind,
    NotificationSource,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.core.notifications.edits import (
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
from squid.core.notifications.errors import NotificationNotFoundError
from squid.core.notifications.models import InboxNotification, NotificationSubscription
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.domain import BuildKind, RecordCriterion, VersionScope
from squid.core.records.domain.models import MaterializationSource
from squid.core.records.models import Record, RecordDefinition, RulesRevision


async def test_follow_labels_resolve_public_names_and_latest_titles(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    async with factory() as uow:
        owner = await uow.accounts.create(consent=AccountConsent.grant_current())
        creator = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.commit()
    async with migrated_session_factory.begin() as session:
        session.add(
            CreatorAlias(
                name="Discovered builder",
                account_id=creator.id,
                claimed_at=Instant.now(),
                claim_method=ClaimMethod.VERIFIED_IGN,
            )
        )
        title = Record(
            criterion=RecordCriterion.SMALLEST,
            build_kind=BuildKind.DOOR,
            version_scope=VersionScope.ALL_TIME,
            category_key="discovery-test",
        )
        session.add(title)
        await session.flush()
        for index, wording in enumerate(("Old wording", "Latest wording")):
            revision = RulesRevision(
                document_hash=f"discovery-{index}", calculator_version="test", formatter_version="test"
            )
            session.add(revision)
            await session.flush()
            session.add(
                RecordDefinition(
                    record_id=title.public_id,
                    rules_revision_id=revision.id,
                    title=wording,
                    materialization_source=MaterializationSource.EAGER,
                )
            )
            await session.flush()
        follows = [
            NotificationSubscription(
                account_id=owner.id, kind=SubscriptionKind.CREATOR, subject_id=creator.public_creator_id
            ),
            NotificationSubscription(account_id=owner.id, kind=SubscriptionKind.RECORD, subject_id=title.public_id),
            NotificationSubscription(account_id=creator.id, kind=SubscriptionKind.RECORD, subject_id=title.public_id),
        ]
        session.add_all(follows)
        await session.flush()
        expected = {follows[0].id: "Discovered builder", follows[1].id: "Latest wording"}
    snapshot = await NotificationService(
        uow_factory=factory, permissions=PermissionService(uow_factory=factory)
    ).editor_snapshot(context=ActingContext.direct(UserActor(account_id=owner.id)))
    assert dict(snapshot.labels) == expected
    executor = EditExecutor(
        factory, {SUBSCRIPTIONS_EDIT: NotificationSubscriptionsEdit(PermissionService(uow_factory=factory))}
    )
    repeated = await executor.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (Revision(notification_target(owner.id), snapshot.preferences.revision),),
            EditValues.of({"create": {"kind": "creator", "subject_id": str(creator.public_creator_id)}}),
        ),
        context=ActingContext.direct(UserActor(account_id=owner.id)),
    )
    assert not repeated.outcome.changed


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
    service = NotificationService(uow_factory=factory, permissions=PermissionService(uow_factory=factory))
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.commit()
    actor = UserActor(account_id=account.id)
    expected = (Revision(notification_target(account.id), 0),)
    results: list[EditResult | RevisionConflictError] = []

    async def edit(kind: str, values: EditValues) -> None:
        try:
            results.append(
                await executor.execute(EditCommand(kind, expected, values), context=ActingContext.direct(actor))
            )
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
    assert (await service.preferences(context=ActingContext.direct(actor))).revision == 1
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), context=ActingContext.direct(actor))
    assert undone.receipt is not None
    assert undone.outcome.revisions[0].value == 2
    await executor.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            undone.outcome.revisions,
            EditValues.of({"create": {"kind": "record_filter", "record_filter": {"build_kinds": ["utility"]}}}),
        ),
        context=ActingContext.direct(actor),
    )
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.redo(undone.outcome.revisions), context=ActingContext.direct(actor))


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
    actor = UserActor(account_id=account.id)
    created = await executor.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (Revision(notification_target(account.id), 0),),
            EditValues.of({"create": {"kind": "record_filter", "record_filter": {"build_kinds": ["door"]}}}),
        ),
        context=ActingContext.direct(actor),
    )
    assert created.receipt is not None
    subscription = SubscriptionsValues.model_validate(created.receipt.after.unpack()).subscriptions[0]
    undone = await executor.execute(
        created.receipt.undo(created.outcome.revisions), context=ActingContext.direct(actor)
    )
    assert undone.receipt is not None
    async with migrated_session_factory() as session:
        row = await session.get(NotificationSubscription, subscription.id)
        assert row is not None
        assert row.enabled is False
    redone = await executor.execute(created.receipt.redo(undone.outcome.revisions), context=ActingContext.direct(actor))
    assert redone.receipt is not None
    assert redone.receipt.after == created.receipt.after
    assert redone.outcome.revisions[0].value == 3
    repeated = await executor.execute(
        EditCommand(SUBSCRIPTIONS_EDIT, redone.outcome.revisions, created.receipt.command.values),
        context=ActingContext.direct(actor),
    )
    assert not repeated.outcome.changed
    assert repeated.outcome.revisions == redone.outcome.revisions
    async with migrated_session_factory() as session:
        assert len((await session.scalars(select(NotificationSubscription))).all()) == 1

    await executor.execute(redone.receipt.undo(redone.outcome.revisions), context=ActingContext.direct(actor))
    row, revision = await NotificationService(
        uow_factory=factory, permissions=PermissionService(uow_factory=factory)
    ).subscription_snapshot(
        context=ActingContext.direct(actor),
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
    service = NotificationService(uow_factory=factory, permissions=PermissionService(uow_factory=factory))
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.notifications.update_preferences(account.id, web_enabled=True, dm_enabled=False)
        await uow.commit()
    actor = UserActor(account_id=account.id)

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
    await executor.execute(
        EditCommand(
            INBOX_EDIT,
            (Revision(inbox_target(second.id), second.revision),),
            EditValues.of({"states": {str(second.id): True}}),
        ),
        context=ActingContext.direct(UserActor(account_id=account.id)),
    )
    with pytest.raises(RevisionConflictError):
        await executor.execute(
            EditCommand(
                INBOX_EDIT,
                (Revision(inbox_target(first.id), 1), Revision(inbox_target(second.id), 1)),
                EditValues.of({"states": {str(first.id): True, str(second.id): True}}),
            ),
            context=ActingContext.direct(actor),
        )
    async with migrated_session_factory() as session:
        current = await session.get(InboxNotification, first.id)
        assert current is not None
        assert current.read_at is None
    saved = await executor.execute(
        EditCommand(
            INBOX_EDIT, (Revision(inbox_target(first.id), 1),), EditValues.of({"states": {str(first.id): True}})
        ),
        context=ActingContext.direct(actor),
    )
    assert saved.receipt is not None
    repeated = await executor.execute(
        EditCommand(INBOX_EDIT, saved.outcome.revisions, saved.receipt.command.values),
        context=ActingContext.direct(actor),
    )
    assert not repeated.outcome.changed
    assert repeated.outcome.revisions == saved.outcome.revisions
    await arrive()
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), context=ActingContext.direct(actor))
    assert undone.outcome.revisions == (Revision(inbox_target(first.id), 3),)

    staff = await arrive(NotificationKind.STAFF_BUILD_SUBMITTED)
    staff_saved = await executor.execute(
        EditCommand(
            INBOX_EDIT, (Revision(inbox_target(staff.id), 1),), EditValues.of({"states": {str(staff.id): True}})
        ),
        context=ActingContext.direct(UserActor(account_id=account.id, is_bot_owner=True)),
    )
    assert staff_saved.receipt is not None
    with pytest.raises(NotificationNotFoundError):
        await executor.execute(
            staff_saved.receipt.undo(staff_saved.outcome.revisions), context=ActingContext.direct(actor)
        )

    repeated = await executor.execute(
        EditCommand(INBOX_EDIT, undone.outcome.revisions, EditValues.of({"states": {str(first.id): False}})),
        context=ActingContext.direct(actor),
    )
    assert not repeated.outcome.changed
    assert repeated.outcome.revisions == undone.outcome.revisions


@pytest.mark.parametrize("gate", ["owner", "web", "consent"])
async def test_read_edit_requires_current_inbox_access(
    migrated_session_factory: async_sessionmaker[AsyncSession], gate: str
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    executor = EditExecutor(factory, {INBOX_EDIT: InboxReadEdit(PermissionService(uow_factory=factory))})
    async with factory() as uow:
        owner = await uow.accounts.create(consent=AccountConsent.grant_current())
        other = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.notifications.update_preferences(owner.id, web_enabled=gate != "web", dm_enabled=False)
        await uow.notifications.update_preferences(other.id, web_enabled=True, dm_enabled=False)
        await uow.commit()
    async with migrated_session_factory.begin() as session:
        event = DomainEvent(event_type="build.confirmed", aggregate_kind="build", aggregate_id=1)
        session.add(event)
        await session.flush()
        kind = NotificationKind.BUILD_CONFIRMED
        item = InboxNotification(
            account_id=owner.id,
            event_id=event.id,
            kind=kind,
            payload={},
            web_visible=True,
            source_key=NotificationSource(event.id, kind).key_for(owner.id),
        )
        session.add(item)
        if gate == "consent":
            account = await session.get_one(Account, owner.id)
            account.consent_version = None
            account.consented_at = None
    command = EditCommand(
        INBOX_EDIT, (Revision(inbox_target(item.id), item.revision),), EditValues.of({"states": {str(item.id): True}})
    )
    with pytest.raises(ConsentRequiredError if gate == "consent" else NotificationNotFoundError):
        await executor.execute(
            command, context=ActingContext.direct(UserActor(account_id=other.id if gate == "owner" else owner.id))
        )

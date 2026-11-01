"""Notification-owned account merge policy, participating in the caller's transaction."""

import uuid

from sqlalchemy import Delete, and_, case, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from whenever import Instant

from squid.core.errors import DataIntegrityError
from squid.notifications.domain import NotificationSource
from squid.notifications.infrastructure.models import (
    NotificationDeliveryRecord,
    NotificationProfile,
    NotificationRecord,
    NotificationSubscriptionRecord,
)


def _merge_notification_profile_statement(*, survivor: int, absorbed: int) -> Insert:
    statement = insert(NotificationProfile).from_select(
        [
            NotificationProfile.account_id,
            NotificationProfile.web_enabled,
            NotificationProfile.dm_enabled,
            NotificationProfile.dm_suspended_at,
            NotificationProfile.created_at,
            NotificationProfile.updated_at,
        ],
        select(
            literal(survivor),
            NotificationProfile.web_enabled,
            NotificationProfile.dm_enabled,
            NotificationProfile.dm_suspended_at,
            NotificationProfile.created_at,
            NotificationProfile.updated_at,
        ).where(NotificationProfile.account_id == absorbed),
    )
    healthy_survivor = and_(NotificationProfile.dm_enabled, NotificationProfile.dm_suspended_at.is_(None))
    healthy_absorbed = and_(statement.excluded.dm_enabled, statement.excluded.dm_suspended_at.is_(None))
    return statement.on_conflict_do_update(
        index_elements=[NotificationProfile.account_id],
        set_={
            "web_enabled": or_(NotificationProfile.web_enabled, statement.excluded.web_enabled),
            "dm_enabled": or_(NotificationProfile.dm_enabled, statement.excluded.dm_enabled),
            "dm_suspended_at": case(
                (or_(healthy_survivor, healthy_absorbed), None),
                else_=func.coalesce(NotificationProfile.dm_suspended_at, statement.excluded.dm_suspended_at),
            ),
        },
    )


def _collapse_notification_subscriptions_statement(*, survivor: int, absorbed: int) -> Delete:
    absorbed_row = aliased(NotificationSubscriptionRecord, name="absorbed_subscription")
    survivor_row = aliased(NotificationSubscriptionRecord, name="survivor_subscription")
    duplicate_ids = (
        select(absorbed_row.id)
        .join(
            survivor_row,
            and_(
                survivor_row.account_id == survivor,
                survivor_row.kind == absorbed_row.kind,
                survivor_row.subject_id.is_not_distinct_from(absorbed_row.subject_id),
                survivor_row.filter.is_not_distinct_from(absorbed_row.filter),
                survivor_row.enabled == absorbed_row.enabled,
            ),
        )
        .where(absorbed_row.account_id == absorbed)
    )
    return delete(NotificationSubscriptionRecord).where(NotificationSubscriptionRecord.id.in_(duplicate_ids))


async def merge_account_notifications(session: AsyncSession, *, survivor: int, absorbed: int) -> None:
    """Merge notification ownership inside the caller's account-merge transaction.

    The caller locks both account rows first. Notification and delivery rows are then
    locked in ID order; no independent transaction or commit may split these writes.
    """
    await session.execute(_merge_notification_profile_statement(survivor=survivor, absorbed=absorbed))
    await session.execute(delete(NotificationProfile).where(NotificationProfile.account_id == absorbed))
    await session.execute(_collapse_notification_subscriptions_statement(survivor=survivor, absorbed=absorbed))
    await session.execute(
        update(NotificationSubscriptionRecord)
        .where(NotificationSubscriptionRecord.account_id == absorbed)
        .values(account_id=survivor)
    )
    await _canonicalize_notifications(session, survivor=survivor, absorbed=absorbed)


async def _canonicalize_notifications(session: AsyncSession, *, survivor: int, absorbed: int) -> None:
    """Rewrite recipient-bound idempotency keys and merge any resulting collisions."""
    notifications = tuple(
        (
            await session.scalars(
                select(NotificationRecord)
                .where(NotificationRecord.account_id.in_((survivor, absorbed)))
                .order_by(NotificationRecord.id)
                .with_for_update()
            )
        ).all()
    )
    notification_ids = tuple(row.id for row in notifications)
    deliveries = tuple(
        (
            await session.scalars(
                select(NotificationDeliveryRecord)
                .where(
                    or_(
                        NotificationDeliveryRecord.notification_id.in_(notification_ids),
                        NotificationDeliveryRecord.account_id.in_((survivor, absorbed)),
                    )
                )
                .order_by(NotificationDeliveryRecord.id)
                .with_for_update()
            )
        ).all()
    )
    delivery_by_notification = {delivery.notification_id: delivery for delivery in deliveries}
    notification_by_id = {notification.id: notification for notification in notifications}
    for delivery in deliveries:
        notification = notification_by_id.get(delivery.notification_id)
        if notification is None:
            msg = "A merged-account delivery points outside the scoped notification set."
            raise DataIntegrityError(
                msg,
                context={"delivery_id": delivery.id, "notification_id": delivery.notification_id},
            )
        if delivery.account_id != notification.account_id:
            msg = "A notification delivery is owned by a different account than its notification."
            raise DataIntegrityError(
                msg,
                context={
                    "delivery_id": delivery.id,
                    "notification_id": notification.id,
                    "delivery_account_id": delivery.account_id,
                    "notification_account_id": notification.account_id,
                },
            )
    # Current keys are unique per recipient, so each absorbed row has at most one survivor.
    survivor_by_source = {
        notification.source_key: notification for notification in notifications if notification.account_id == survivor
    }

    for absorbed_notification in notifications:
        try:
            source = NotificationSource.parse(
                absorbed_notification.source_key,
                event_id=absorbed_notification.event_id,
                kind=absorbed_notification.kind,
                account_id=absorbed_notification.account_id,
                payload=absorbed_notification.payload,
            )
        except DataIntegrityError as error:
            raise DataIntegrityError(
                str(error), context={**error.context, "notification_id": absorbed_notification.id}
            ) from error
        if absorbed_notification.account_id == survivor:
            continue
        canonical_source = source.key_for(survivor)
        survivor_notification = survivor_by_source.get(canonical_source)
        absorbed_delivery = delivery_by_notification.get(absorbed_notification.id)
        if survivor_notification is None:
            absorbed_notification.account_id = survivor
            absorbed_notification.source_key = canonical_source
            if absorbed_delivery is not None:
                _fence_merged_delivery(absorbed_delivery, survivor)
            continue

        if (
            survivor_notification.event_id != absorbed_notification.event_id
            or survivor_notification.kind != absorbed_notification.kind
            or survivor_notification.payload != absorbed_notification.payload
        ):
            msg = "Account merge found incompatible notifications with the same canonical source key."
            raise DataIntegrityError(msg, context={"source_key": canonical_source})
        survivor_notification.web_visible = survivor_notification.web_visible or absorbed_notification.web_visible
        survivor_notification.read_at = _earliest_present(
            survivor_notification.read_at,
            absorbed_notification.read_at,
        )
        survivor_notification.created_at = min(survivor_notification.created_at, absorbed_notification.created_at)
        survivor_delivery = delivery_by_notification.get(survivor_notification.id)
        if survivor_delivery is None and absorbed_delivery is not None:
            absorbed_delivery.notification_id = survivor_notification.id
            _fence_merged_delivery(absorbed_delivery, survivor)
        elif survivor_delivery is not None and absorbed_delivery is not None:
            _coalesce_notification_deliveries(survivor_delivery, absorbed_delivery, survivor)
            await session.delete(absorbed_delivery)
        elif survivor_delivery is not None:
            _fence_merged_delivery(survivor_delivery, survivor)
        await session.delete(absorbed_notification)


def _earliest_present(left: Instant | None, right: Instant | None) -> Instant | None:
    values = tuple(value for value in (left, right) if value is not None)
    return min(values) if values else None


def _fence_merged_delivery(delivery: NotificationDeliveryRecord, account_id: int) -> None:
    delivery.account_id = account_id
    delivery.generation += 1
    delivery.nonce = uuid.uuid4()
    delivery.claimed_at = None
    delivery.claim_token = None


def _coalesce_notification_deliveries(
    survivor: NotificationDeliveryRecord,
    absorbed: NotificationDeliveryRecord,
    account_id: int,
) -> None:
    """Keep one delivery without forgetting a completed send or reviving two dead attempts."""
    survivor.account_id = account_id
    survivor.generation = max(survivor.generation, absorbed.generation) + 1
    survivor.nonce = uuid.uuid4()
    survivor.claimed_at = None
    survivor.claim_token = None
    survivor.attempts = max(survivor.attempts, absorbed.attempts)
    survivor.sent_at = _earliest_present(survivor.sent_at, absorbed.sent_at)
    if survivor.sent_at is not None:
        survivor.dead_at = None
        survivor.last_error = None
        return
    live = tuple(delivery for delivery in (survivor, absorbed) if delivery.dead_at is None)
    if live:
        survivor.available_at = min(delivery.available_at for delivery in live)
        survivor.dead_at = None
        survivor.last_error = next((delivery.last_error for delivery in live if delivery.last_error is not None), None)
        return
    survivor.dead_at = max(value for value in (survivor.dead_at, absorbed.dead_at) if value is not None)
    survivor.last_error = absorbed.last_error or survivor.last_error

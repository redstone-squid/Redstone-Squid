"""Focused contracts for recipient-key and delivery-state account merging."""

import uuid

import pytest

from squid.accounts.infrastructure.repository import (
    _AccountMergeContext,
    _canonical_notification_source_key,
    _coalesce_notification_deliveries,
)
from squid.notifications.domain import NotificationKind
from squid.notifications.infrastructure.models import NotificationDeliveryRecord, NotificationRecord
from squid.persistence.types import now


def _notification(kind: NotificationKind, source_key: str) -> NotificationRecord:
    row = NotificationRecord(
        account_id=9,
        event_id=3,
        source_key=source_key,
        kind=kind,
        payload={"build_id": 42},
        web_visible=True,
    )
    row.id = 1
    return row


@pytest.mark.parametrize(
    ("kind", "source_key", "expected"),
    [
        (NotificationKind.STAFF_BUILD_SUBMITTED, "event:3:staff:9", "event:3:staff:7"),
        (NotificationKind.BUILD_CONFIRMED, "event:3:owner:9", "event:3:owner:7"),
        (NotificationKind.CREATOR_BUILD_CONFIRMED, "event:3:creator:9", "event:3:creator:7"),
        (NotificationKind.RECORD_GAINED, "event:3:record-build:42:account:9", "event:3:record-build:42:account:7"),
        (NotificationKind.RECORD_GAINED, "event:3:record-build:42:user:9", "event:3:record-build:42:account:7"),
    ],
)
def test_notification_source_keys_follow_the_survivor_and_upgrade_legacy_record_keys(
    kind: NotificationKind,
    source_key: str,
    expected: str,
) -> None:
    assert (
        _canonical_notification_source_key(
            _notification(kind, source_key),
            _AccountMergeContext(survivor=7, absorbed=9),
        )
        == expected
    )


def test_delivery_collision_keeps_live_work_and_fences_both_prior_claims() -> None:
    timestamp = now()
    survivor = NotificationDeliveryRecord(
        notification_id=1,
        account_id=7,
        generation=2,
        nonce=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        available_at=timestamp.add(minutes=5),
        attempts=3,
        dead_at=timestamp,
        last_error="dead survivor",
    )
    survivor.id = 1
    absorbed = NotificationDeliveryRecord(
        notification_id=2,
        account_id=9,
        generation=4,
        nonce=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        available_at=timestamp,
        claimed_at=timestamp,
        claim_token=uuid.UUID("33333333-3333-4333-8333-333333333333"),
        attempts=2,
        last_error="retryable",
    )
    absorbed.id = 2

    _coalesce_notification_deliveries(survivor, absorbed, 7)

    assert survivor.account_id == 7
    assert survivor.generation == 5
    assert survivor.nonce not in {absorbed.nonce, uuid.UUID("11111111-1111-4111-8111-111111111111")}
    assert survivor.claimed_at is None
    assert survivor.claim_token is None
    assert survivor.attempts == 3
    assert survivor.available_at == timestamp
    assert survivor.dead_at is None
    assert survivor.last_error == "retryable"


def test_delivery_collision_never_forgets_a_completed_send() -> None:
    timestamp = now()
    survivor = NotificationDeliveryRecord(notification_id=1, account_id=7, available_at=timestamp)
    survivor.id = 1
    absorbed = NotificationDeliveryRecord(
        notification_id=2,
        account_id=9,
        available_at=timestamp,
        sent_at=timestamp,
        last_error="obsolete",
    )
    absorbed.id = 2

    _coalesce_notification_deliveries(survivor, absorbed, 7)

    assert survivor.sent_at == timestamp
    assert survivor.dead_at is None
    assert survivor.last_error is None

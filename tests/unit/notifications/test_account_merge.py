"""Focused contracts for recipient-key and delivery-state account merging."""

import uuid

import pytest

from squid.core.errors import DataIntegrityError
from squid.notifications.domain import NotificationKind, NotificationSource
from squid.notifications.infrastructure.account_merge import _coalesce_notification_deliveries
from squid.notifications.infrastructure.models import NotificationDeliveryRecord
from squid.persistence.types import now


@pytest.mark.parametrize(
    ("kind", "source_key", "expected"),
    [
        (NotificationKind.STAFF_BUILD_SUBMITTED, "event:3:staff:9", "event:3:staff:7"),
        (NotificationKind.BUILD_CONFIRMED, "event:3:owner:9", "event:3:owner:7"),
        (NotificationKind.BUILD_DENIED, "event:3:owner:9", "event:3:owner:7"),
        (NotificationKind.CREATOR_BUILD_CONFIRMED, "event:3:creator:9", "event:3:creator:7"),
        (NotificationKind.RECORD_GAINED, "event:3:record-build:42:account:9", "event:3:record-build:42:account:7"),
    ],
)
def test_notification_source_keys_follow_the_survivor(
    kind: NotificationKind,
    source_key: str,
    expected: str,
) -> None:
    assert NotificationSource(3, kind, build_id=42).key_for(7) == expected
    assert (
        NotificationSource.parse(source_key, event_id=3, kind=kind, account_id=9, payload={"build_id": 42}).key_for(7)
        == expected
    )


@pytest.mark.parametrize(
    ("kind", "source_key"),
    [
        (NotificationKind.BUILD_CONFIRMED, "event:999:owner:9"),
        (NotificationKind.BUILD_CONFIRMED, "event:3:creator:9"),
        (NotificationKind.RECORD_GAINED, "event:3:record-build:99:account:9"),
        (NotificationKind.BUILD_CONFIRMED, "event:3:owner:7"),
    ],
)
def test_notification_source_keys_validate_the_complete_event_kind_and_payload_grammar(
    kind: NotificationKind,
    source_key: str,
) -> None:
    with pytest.raises(DataIntegrityError, match="does not match"):
        NotificationSource.parse(source_key, event_id=3, kind=kind, account_id=9, payload={"build_id": 42}).key_for(7)


@pytest.mark.parametrize("build_id", [None, True, "42"])
def test_record_source_rejects_missing_or_malformed_payload_build_id(build_id: object) -> None:
    with pytest.raises(DataIntegrityError, match="build identifier"):
        NotificationSource.parse(
            "event:3:record-build:42:account:9",
            event_id=3,
            kind=NotificationKind.RECORD_GAINED,
            account_id=9,
            payload={"build_id": build_id},
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

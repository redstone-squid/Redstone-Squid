"""Notification filter and delivery rendering contracts."""

from uuid import UUID

import pytest

from squid.bot.notifications import NotificationCog, render_delivery
from squid.notifications import PendingNotificationDelivery, RecordSubscriptionFilter, TagPredicate
from squid.notifications.domain import NotificationKind


def test_record_filter_round_trips_presence_and_exact_tag_predicates() -> None:
    record_filter = RecordSubscriptionFilter(
        build_kinds=frozenset({"door"}),
        record_classes=frozenset({"fastest"}),
        tags=(TagPredicate(4), TagPredicate(7, "exact", 3.5)),
    )

    assert RecordSubscriptionFilter.from_dict(record_filter.as_dict()) == record_filter


def test_empty_record_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one predicate"):
        RecordSubscriptionFilter()


def test_record_filter_rejects_values_outside_the_stable_contract() -> None:
    with pytest.raises(ValueError, match="unsupported build kind"):
        RecordSubscriptionFilter(build_kinds=frozenset({"piston"}))


def test_record_filter_rejects_ambiguous_duplicate_tag_predicates() -> None:
    with pytest.raises(ValueError, match="one predicate per tag"):
        RecordSubscriptionFilter(tags=(TagPredicate(4), TagPredicate(4, "exact", value=True)))


def test_presence_and_exact_predicates_enforce_distinct_shapes() -> None:
    with pytest.raises(ValueError, match="cannot include"):
        TagPredicate(4, "present", value=True)
    with pytest.raises(ValueError, match="require a value"):
        TagPredicate(4, "exact")


def test_delivery_renderer_uses_configured_public_site_link() -> None:
    delivery = PendingNotificationDelivery(
        id=1,
        generation=1,
        discord_id=2,
        nonce=UUID("11111111-1111-1111-1111-111111111111"),
        claim_token=UUID("22222222-2222-2222-2222-222222222222"),
        attempts=1,
        kind=NotificationKind.RECORD_GAINED,
        payload={"build_id": 42, "records": [{"title": "Fastest"}, {"title": "Smallest"}]},
    )

    assert render_delivery(delivery, "https://example.test") == (
        "A credited build gained 2 records.\nhttps://example.test/builds/42"
    )


def test_staff_submission_delivery_uses_pending_review_command_not_public_link() -> None:
    delivery = PendingNotificationDelivery(
        id=1,
        generation=1,
        discord_id=2,
        nonce=UUID("11111111-1111-1111-1111-111111111111"),
        claim_token=UUID("22222222-2222-2222-2222-222222222222"),
        attempts=1,
        kind=NotificationKind.STAFF_BUILD_SUBMITTED,
        payload={"build_id": 42},
    )

    rendered = render_delivery(delivery, "https://example.test")

    assert rendered == "A new build is awaiting staff review.\nOpen it in Discord with `/build browse id:42`."
    assert "https://example.test/builds/42" not in rendered


def test_notification_management_is_one_slash_workspace() -> None:
    assert NotificationCog.__cog_commands__ == []
    assert {command.name for command in NotificationCog.__cog_app_commands__} == {"notifications"}

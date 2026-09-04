"""Notification materialization matching contracts."""

import re
from decimal import Decimal
from typing import cast

from sqlalchemy import Table

from squid.notifications.domain import NotificationKind, SubscriptionKind
from squid.notifications.infrastructure.models import NotificationRecord, NotificationSubscriptionRecord
from squid.notifications.infrastructure.repository import _exact_value


def test_exact_tag_matching_does_not_cross_boolean_and_numeric_types() -> None:
    assert _exact_value(Decimal("1"), 1) is True
    assert _exact_value(Decimal("1"), "1.0") is True
    assert _exact_value(Decimal("1"), expected=True) is False
    assert _exact_value(stored=True, expected=True) is True
    assert _exact_value(stored=True, expected=1) is False
    assert _exact_value("1", 1) is False


def test_persisted_kind_checks_are_total_over_domain_enums() -> None:
    constraints = {
        constraint.name: str(constraint.sqltext)
        for model in (NotificationRecord, NotificationSubscriptionRecord)
        for constraint in cast(Table, model.__table__).constraints
        if constraint.name is not None and hasattr(constraint, "sqltext")
    }

    assert set(re.findall(r"'([^']+)'", constraints["notifications_kind_check"])) == {
        kind.value for kind in NotificationKind
    }
    assert set(re.findall(r"'([^']+)'", constraints["notification_subscriptions_kind_check"])) == {
        kind.value for kind in SubscriptionKind
    }

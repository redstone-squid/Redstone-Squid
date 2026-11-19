"""Shared notification summaries and details for DMs and the inbox."""

from collections.abc import Mapping

from squid.core.i18n import tr
from squid.core.notifications.domain import NotificationKind
from squid_i18n import Text, md_tr


def notification_build_id(payload: Mapping[str, object]) -> int | None:
    value = payload.get("build_id")
    return value if type(value) is int and value > 0 else None


def notification_summary(kind: NotificationKind, payload: Mapping[str, object]) -> Text:
    build_id = notification_build_id(payload)
    build = (
        tr(t"notifications-build-number = Build #{build_id!s}") if build_id else tr(t"notifications-build = A build")
    )
    match kind:
        case NotificationKind.BUILD_CONFIRMED:
            return tr(t"notifications-confirmed-summary = {build} was confirmed.")
        case NotificationKind.BUILD_DENIED:
            return tr(t"notifications-denied-summary = {build} was denied.")
        case NotificationKind.CREATOR_BUILD_CONFIRMED:
            return tr(t"notifications-creator-summary = {build} by a creator you follow was confirmed.")
        case NotificationKind.STAFF_BUILD_SUBMITTED:
            return tr(t"notifications-review-summary = {build} is awaiting staff review.")
        case NotificationKind.RECORD_GAINED:
            records = payload.get("records")
            if not isinstance(records, list) or not records:
                return tr(t"notifications-record-summary-unknown = {build} gained records.")
            count = len(records)
            return tr(t"""
                notifications-record-summary = {{ $count ->
                    [one] {build} gained {count} record.
                   *[other] {build} gained {count} records.
                }}
            """)
    return tr(t"notifications-update-summary = {build} has an update.")


def notification_details(kind: NotificationKind, payload: Mapping[str, object]) -> Text:
    """Show record names without letting large record groups overwhelm a message."""
    summary = notification_summary(kind, payload)
    records = payload.get("records")
    if kind is not NotificationKind.RECORD_GAINED or not isinstance(records, list):
        return summary
    lines: list[Text] = [summary]
    for record in records[:5]:
        if isinstance(record, dict) and isinstance(title := record.get("title"), str) and title.strip():
            title = " ".join(title.split())
            title = title if len(title) <= 120 else title[:119] + "…"
            lines.append(md_tr(t"notifications-record-detail = • {title}"))
    if (remaining := len(records) - 5) > 0:
        lines.append(tr(t"notifications-more-records = …and {remaining} more records."))
    return Text.join("\n", lines)

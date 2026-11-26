"""Shared notification summaries and details for DMs and the inbox."""

from squid.core.i18n import tr
from squid.core.notifications.domain import NotificationKind, NotificationMessage
from squid_i18n import Text, md_tr


def notification_summary(message: NotificationMessage) -> Text:
    if message.build is None:
        build = tr(t"notifications-build = A build")
    else:
        build_id = message.build.id
        build = tr(t"notifications-build-number = Build #{build_id!s}")
    count = len(message.records)
    match message.kind:
        case NotificationKind.BUILD_CONFIRMED:
            return tr(t"notifications-confirmed-summary = {build} was approved.")
        case NotificationKind.BUILD_DENIED:
            return tr(t"notifications-denied-summary = {build} was rejected.")
        case NotificationKind.CREATOR_BUILD_CONFIRMED:
            return tr(t"notifications-creator-summary = {build} by a creator you follow was approved.")
        case NotificationKind.STAFF_BUILD_SUBMITTED:
            return tr(t"notifications-review-summary = {build} is awaiting staff review.")
        case NotificationKind.RECORD_GAINED:
            return tr(t"""
                notifications-record-summary = {{ $count ->
                    [one] {build} gained {count} record.
                   *[other] {build} gained {count} records.
                }}
            """)
        case NotificationKind.RECORD_RETIRED:
            return tr(t"""
                notifications-retired-summary = {{ $count ->
                    [one] A record you follow no longer has any eligible builds.
                   *[other] {count} records you follow no longer have any eligible builds.
                }}
            """)


def notification_details(message: NotificationMessage) -> Text:
    """Show record names without letting large record groups overwhelm a message."""
    lines: list[Text] = [notification_summary(message)]
    for record in message.records[:5]:
        if title := " ".join(record.title.split()):
            title = title if len(title) <= 120 else title[:119] + "…"
            lines.append(md_tr(t"notifications-record-detail = • {title}"))
    if (remaining := len(message.records) - 5) > 0:
        lines.append(tr(t"notifications-more-records = …and {remaining} more records."))
    return Text.join("\n", lines)

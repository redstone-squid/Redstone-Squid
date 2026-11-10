"""Text summaries shared by developer commands and the interactive inspector."""

from typing import TYPE_CHECKING

from squid_reactivity.topics import BusSnapshot
from squid_ui_discord.message_root_scheduler import MessageRootSchedulerSnapshot

if TYPE_CHECKING:
    # Keep the optional durable extra out of runtime imports.
    from squid_ui_discord.durability import RecoveryReport


def scheduler_summary(snapshot: MessageRootSchedulerSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"queued={snapshot.queued} in_flight={snapshot.in_flight} failed={snapshot.failed}"


def topic_summary(snapshot: BusSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"known={len(snapshot.topics)} queued={snapshot.queued} failed={snapshot.failed}"


def recovery_summary(report: RecoveryReport | None) -> str:
    if report is None:
        return "never"
    return f"restored={len(report.restored)} failed={len(report.failed)}"

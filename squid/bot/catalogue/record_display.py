"""Shared record rows for catalogue browsing and search."""

from collections.abc import Awaitable, Callable, Sequence
from functools import partial

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import tr
from squid.core.records.domain.models import BuildKind, RecordCriterion, VersionScope
from squid.core.vocabulary import BUILD_KINDS, VERSION_SCOPES
from squid_i18n import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target


def record_row(title: sl.TextLike, subtitle: sl.TextLike | None) -> Text:
    """Keep the qualifier visible in compact lists of similarly named records."""
    return Text(t"{title} · {subtitle}") if subtitle else Text(title)


def record_standings(
    title: sl.TextLike,
    subtitle: str | None,
    criterion: RecordCriterion,
    version_scope: VersionScope,
    holders: Sequence[int],
    open_build: Callable[[int, sl.PressEvent], Awaitable[None]],
    *,
    build_kind: BuildKind | None = None,
    current_holder: int | None = None,
    details: Sequence[sl.LayoutNode[ComponentsV2Target, sd.Request]] = (),
) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
    """Show the record and its holders, with room for publication or search details."""
    scope = VERSION_SCOPES[version_scope]
    others = tuple(build_id for build_id in holders if build_id != current_holder)
    return sl.section(
        sl.heading(title),
        sl.paragraph(subtitle) if subtitle else None,
        sl.note(Text(t"{BUILD_KINDS[build_kind]} · {scope}") if build_kind else scope),
        sl.paragraph(rule) if (rule := _comparison_rule(criterion)) else None,
        *details,
        sl.note(tr(t"search-grouped-tied-holders = Tied holders")) if len(holders) > 1 else None,
        sl.action_controls(
            *(
                sl.action_control(
                    tr(t"record-browse-open-holder = Open build #{build_id!s}"),
                    partial(open_build, build_id),
                    key=f"holder-{build_id}",
                )
                for build_id in others
            ),
            key="record-holders",
        )
        if others
        else None,
        sl.note(tr(t"record-browse-no-holders = No record holders yet.")) if not holders else None,
    )


def _comparison_rule(criterion: RecordCriterion) -> sl.TextLike | None:
    match criterion:
        case RecordCriterion.FIRST:
            return tr(t"record-rule-first = Earliest known completion date.")
        case RecordCriterion.SMALLEST:
            return tr(t"record-rule-smallest = Lowest volume; earliest completion breaks a tie.")
        case RecordCriterion.FASTEST:
            return tr(t"record-rule-fastest = Fastest worst-case timing; earliest completion breaks a tie.")
        case RecordCriterion.FASTEST_SMALLEST:
            return tr(t"record-rule-fastest-smallest = Lowest volume, then fastest timing, then earliest completion.")
        case RecordCriterion.SMALLEST_FASTEST:
            return tr(t"record-rule-smallest-fastest = Fastest timing, then lowest volume, then earliest completion.")
        case _:
            return None

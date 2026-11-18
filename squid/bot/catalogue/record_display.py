"""Shared record rows and standings for catalogue browsing and search."""

from collections.abc import Awaitable, Callable, Sequence
from functools import partial

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import tr
from squid.records.domain.models import BaseTitle
from squid_i18n import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target


def record_row(title: sl.TextLike, subtitle: str | None) -> Text:
    """Keep the qualifier visible in compact lists of similarly named records."""
    return Text(t"{title} · {subtitle}") if subtitle else Text(title)


def record_standings(
    title: sl.TextLike,
    subtitle: str | None,
    base_title: str,
    version_scope: str,
    holders: Sequence[int],
    open_build: Callable[[int, sl.PressEvent], Awaitable[None]],
    *,
    build_kind: str | None = None,
    current_holder: int | None = None,
    details: Sequence[sl.LayoutNode[ComponentsV2Target, sd.Request]] = (),
) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
    """Show the record and its holders, with room for publication or search details."""
    scope = version_scope.replace("_", " ")
    others = tuple(build_id for build_id in holders if build_id != current_holder)
    return sl.section(
        sl.heading(title),
        sl.paragraph(subtitle) if subtitle else None,
        sl.note(f"{build_kind} · {scope}" if build_kind else scope),
        sl.paragraph(rule) if (rule := _comparison_rule(base_title)) else None,
        *details,
        sl.note(tr(t"search-grouped-tied-holders = Tied holders")) if others and current_holder is not None else None,
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
        sl.note(tr(t"record-browse-no-holders = No confirmed holders yet.")) if not holders else None,
    )


def _comparison_rule(base_title: str) -> sl.TextLike | None:
    match base_title:
        case BaseTitle.FIRST:
            return tr(t"record-rule-first = Earliest known completion date.")
        case BaseTitle.SMALLEST:
            return tr(t"record-rule-smallest = Lowest volume; earliest completion breaks a tie.")
        case BaseTitle.FASTEST:
            return tr(t"record-rule-fastest = Fastest worst-case timing; earliest completion breaks a tie.")
        case BaseTitle.FASTEST_SMALLEST:
            return tr(t"record-rule-fastest-smallest = Lowest volume, then fastest timing, then earliest completion.")
        case BaseTitle.SMALLEST_FASTEST:
            return tr(t"record-rule-smallest-fastest = Fastest timing, then lowest volume, then earliest completion.")
        case _:
            return None

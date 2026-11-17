"""Search reference and runnable examples for the catalogue's two scopes."""

from collections.abc import Awaitable, Callable
from functools import partial

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.builds.domain import BuildCategory
from squid.records.domain import BaseTitle, BuildKind, VersionScope
from squid_i18n import md_tr
from squid_ui.authoring.semantic import Fields, Paragraph
from squid_ui.content.text import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target

# Examples are also parsed in tests so the reference cannot drift from the query language.
BUILD_EXAMPLES = (
    (tr(t"search-example-compact = Compact doors"), "door width<=5 height<=6"),
    (tr(t"search-example-exclude = Exclude a restriction"), "door NOT restriction:observerless"),
    (tr(t"search-example-dates = Completion date range"), "completion_at:[2025-01-01 TO 2026-01-01]"),
)
RECORD_EXAMPLES = (
    (tr(t"search-example-fastest = All-time fastest doors"), "kind:door base_title:fastest version_scope:all_time"),
    (
        tr(t"search-example-extenders = Current smallest extenders"),
        "kind:extender base_title:smallest version_scope:up_to_date",
    ),
    (
        tr(t"search-example-combined = Combined record titles"),
        "base_title:fastest_smallest OR base_title:smallest_fastest",
    ),
)


def search_help(
    *,
    records: bool,
    apply_example: Callable[[str, sl.PressEvent], Awaitable[None]],
) -> sp.Tabs[ComponentsV2Target, sd.Request]:
    """Build the tabbed search reference shown above the catalogue's active view."""
    if records:
        base_titles = ", ".join(title.value for title in BaseTitle)
        kinds = ", ".join(kind.value for kind in BuildKind)
        scopes = ", ".join(scope.value for scope in VersionScope)
        fields = (
            sl.paragraph(
                tr(
                    t"search-reference-record-scope = Record filters describe the record. To filter by build size "
                    t"or creator, search Builds instead."
                )
            ),
            _entry(
                tr(t"search-reference-label-record-type = Record type"),
                md_tr(t"search-reference-record-type = `base_title:` {base_titles}"),
            ),
            _entry(
                tr(t"search-reference-label-kind = Kind"),
                md_tr(t"search-reference-kind = `kind:` {kinds}"),
            ),
            _entry(
                tr(t"search-reference-label-version-scope = Version scope"),
                md_tr(t"search-reference-version-scope = `version_scope:` {scopes}"),
            ),
            _entry(
                tr(t"search-reference-label-title = Title words"),
                md_tr(t"search-reference-record-title = `title:door`"),
            ),
            sl.paragraph(
                tr(
                    t"search-reference-record-types = The Smallest, Fastest and First buttons match records of any "
                    t"selected type, together with your query."
                )
            ),
        )
    else:
        categories = ", ".join(kind.value for kind in BuildCategory)
        fields = (
            sl.paragraph(
                md_tr(
                    t"search-reference-intro = Type words to match titles and descriptions. Add filter lists every "
                    t"field and its known values, or type filters yourself, like `width<=5`."
                )
            ),
            _entry(
                tr(t"search-reference-label-kind = Kind"),
                md_tr(t"search-reference-build-kind = `kind:door` · {categories}"),
            ),
            _entry(
                tr(t"search-reference-label-size = Size"),
                md_tr(
                    t"search-reference-size = `width<=5`, `height:[3 TO 5]`, `depth`, `volume`. The whole build in "
                    t"blocks, not the door opening."
                ),
            ),
            _entry(
                tr(t"search-reference-label-credit = Creator and version"),
                md_tr(t'search-reference-credit = `creator:Steve`, `version:"Java 1.20"`'),
            ),
            _entry(
                tr(t"search-reference-label-tags = Restrictions, patterns and tags"),
                md_tr(
                    t'search-reference-tags = `restriction:observerless`, `pattern:"full seamless"`, `tag:`. '
                    t"Quote names with spaces."
                ),
            ),
            _entry(
                tr(t"search-reference-label-dates = Dates"),
                md_tr(
                    t"search-reference-dates = `completion_at>=2025-01-01`. Also `created_at` (submitted) and "
                    t"`updated_at`."
                ),
            ),
        )
    examples = RECORD_EXAMPLES if records else BUILD_EXAMPLES
    example_nodes = (
        sl.paragraph(
            Text.join(
                "\n\n",
                (md_tr(t"search-reference-example-description = **{title}** — `{query}`") for title, query in examples),
            )
        ),
        sl.action_controls(
            *(
                sl.action_control(title, partial(apply_example, query), key=f"example-{index}")
                for index, (title, query) in enumerate(examples)
            ),
            key="example-actions",
        ),
    )
    return sp.Tabs(
        (
            sp.Tab(
                "fields",
                tr(t"search-reference-fields = What you can search"),
                sl.section(sl.heading(tr(t"search-reference-fields = What you can search")), _reference(fields)),
            ),
            sp.Tab(
                "examples",
                tr(t"search-reference-examples = Examples"),
                (
                    sl.note(
                        tr(
                            t"search-reference-example-reset = Running an example replaces your current query, "
                            t"type buttons and sort."
                        )
                    ),
                    *example_nodes,
                ),
            ),
            sp.Tab(
                "syntax",
                tr(t"search-reference-syntax = Combining filters"),
                sl.section(
                    sl.heading(tr(t"search-reference-syntax = Combining filters")),
                    sl.paragraph(
                        Text.join(
                            "\n",
                            (
                                md_tr(t"search-reference-and = - Everything you type must match: `door width<=5`."),
                                md_tr(
                                    t"search-reference-or = - `OR` matches either side. Group with brackets: "
                                    t"`(door OR extender) width<=5`."
                                ),
                                md_tr(
                                    t"search-reference-not = - `NOT` leaves matches out: "
                                    t"`door NOT restriction:observerless`."
                                ),
                                md_tr(
                                    t'search-reference-operators = - Quote phrases: `title:"vault door"`. Compare '
                                    t"numbers and dates with `<`, `<=`, `>`, `>=` or a range like `[3 TO 5]`."
                                ),
                                md_tr(t"search-reference-sort = - Order results with Sort, not in the query."),
                                tr(
                                    t"search-reference-empty = - No results? Remove filters one at a time. Filters "
                                    t"never reveal hidden builds."
                                ),
                            ),
                        )
                    ),
                ),
            ),
        ),
        key="search-reference",
        title=tr(t"catalogue-search-help = Search help"),
    )


def _entry(label: sl.TextLike, value: sl.TextLike) -> Fields:
    return sl.fields(sl.field(label, value))


def _reference(entries: tuple[Fields | Paragraph, ...]) -> sl.LayoutNode[ComponentsV2Target]:
    lines: list[sl.TextLike] = []
    for entry in entries:
        if isinstance(entry, Paragraph):
            lines.append(entry.content)
        else:
            lines.extend(Text(t"{field.label}: {field.value}") for field in entry.fields)
    source = sl.stack(*entries, key="entries")
    return sl.alternatives(
        sl.alternative(source, key="fields", fidelity=sl.equivalent()),
        sl.alternative(
            sl.paragraph(Text.join("\n\n", lines), key="compact"),
            key="compact",
            fidelity=sl.equivalent(),
            preference=1,
            correspondence=(sl.corresponds("compact", "entries"),),
        ),
        content=source,
        key="reference",
    )

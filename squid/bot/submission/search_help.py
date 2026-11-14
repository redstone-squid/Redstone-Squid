"""Search reference and runnable examples for the catalogue's two scopes."""

from collections.abc import Awaitable, Callable
from functools import partial

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.builds.domain import BuildCategory
from squid.records.domain import BaseTitle, BuildKind, VersionScope
from squid_i18n import md_tr
from squid_ui.semantic import Fields, Paragraph
from squid_ui.text import Text

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
) -> sp.Tabs[sl.ComponentsV2Target, sd.Request]:
    """Build the tabbed reference shown in place of browser results."""
    if records:
        fields = (
            sl.paragraph(
                tr(
                    t"search-reference-record-scope = Record filters describe the record category. Build "
                    t"dimensions and creator filters belong in Builds."
                )
            ),
            _entry("title", tr(t"search-reference-record-title = Words in the indexed title; for example title:door.")),
            _entry("kind", ", ".join(kind.value for kind in BuildKind)),
            _entry("base_title", ", ".join(title.value for title in BaseTitle)),
            _entry("version_scope", ", ".join(scope.value for scope in VersionScope)),
            sl.paragraph(
                tr(
                    t"search-reference-record-types = The type toggles match any selected type, then combine "
                    t"with your typed query. Use base_title for combined titles such as fastest_smallest."
                )
            ),
        )
    else:
        fields = (
            _entry("kind", ", ".join(kind.value for kind in BuildCategory)),
            _entry(
                "title · description",
                tr(t"search-reference-text = Words or quoted phrases in the title or description."),
            ),
            _entry(
                "creator · version",
                tr(t'search-reference-credit = Creator name or Minecraft version; creator:Steve, version:"Java 1.20".'),
            ),
            _entry(
                "tag · restriction · type · pattern",
                tr(t"search-reference-tags = A known tag, restriction or pattern name. Quote names containing spaces."),
            ),
            _entry(
                "width · height · depth",
                md_tr(
                    t"search-reference-size = Physical build dimensions in blocks and volume in blocks "
                    t"cubed, not the door opening size. Use width<=5 or width:[3 TO 5].",
                ),
            ),
            _entry(
                "completion_at · created_at · updated_at",
                tr(
                    t"search-reference-dates = Completion, submission and update dates. Use YYYY-MM-DD, for "
                    t"example completion_at>=2025-01-01."
                ),
            ),
            sl.paragraph(
                tr(
                    t"search-reference-autocomplete = For suggested field names and values, use the query "
                    t"option of /search. Discord modals cannot show those suggestions."
                )
            ),
        )
    examples = RECORD_EXAMPLES if records else BUILD_EXAMPLES
    example_nodes = tuple(
        sl.section(
            sl.heading(title, level=3),
            sl.code(query),
            sl.action_controls(
                sl.action_control(
                    tr(t"search-reference-try = Try this search"),
                    partial(apply_example, query),
                    key=f"example-{index}",
                ),
                key=f"example-actions-{index}",
            ),
        )
        for index, (title, query) in enumerate(examples)
    )
    return sp.Tabs(
        (
            sp.Tab(
                "fields",
                tr(t"search-reference-fields = Fields & values"),
                sl.section(sl.heading(tr(t"search-reference-fields = Fields & values")), _reference(fields)),
            ),
            sp.Tab(
                "examples",
                tr(t"search-reference-examples = Worked examples"),
                (
                    sl.note(
                        tr(
                            t"search-reference-example-reset = Try a search to replace your current query, "
                            t"type toggles and sort order."
                        )
                    ),
                    *example_nodes,
                ),
            ),
            sp.Tab(
                "syntax",
                tr(t"search-reference-syntax = Syntax & sorting"),
                sl.section(
                    sl.heading(tr(t"search-reference-syntax = Syntax & sorting")),
                    sl.paragraph(
                        tr(
                            t"search-reference-and = Spaces mean AND: door width<=5 requires both. OR "
                            t"accepts either: (door OR extender) width<=5 applies the width limit to both "
                            t"alternatives."
                        )
                    ),
                    sl.paragraph(
                        tr(
                            t"search-reference-not = NOT excludes a match: door NOT "
                            t"restriction:observerless. AND binds more tightly than OR; use parentheses to "
                            t"group alternatives."
                        )
                    ),
                    sl.paragraph(
                        md_tr(
                            t'search-reference-operators = Quote phrases: title:"vault door". Numbers and '
                            t"dates support <, <=, >, >= and inclusive ranges such as width:[3 TO 5]. "
                            t"Wildcards (* and ?) are not supported.",
                        )
                    ),
                    sl.paragraph(
                        tr(
                            t"search-reference-sort = Put one field in Sort, not in the query. Available "
                            t"fields: width, height, depth, created_at, updated_at. Prefix with - to reverse "
                            t"order. Dimensions apply to Builds. Blank uses relevance for keywords; "
                            t"filter-only searches use the default result order."
                        )
                    ),
                    sl.paragraph(
                        tr(
                            t"search-reference-empty = No matches? Turn off type toggles, remove one filter "
                            t"at a time, and check Fields & values. Records only includes populated "
                            t"standings; hidden builds are never made visible by a filter."
                        )
                    ),
                ),
            ),
        ),
        key="search-reference",
        title=tr(t"catalogue-search-help = Search help"),
    )


def _entry(label: str, value: sl.TextLike) -> Fields:
    return sl.fields(sl.field(label, value))


def _reference(entries: tuple[Fields | Paragraph, ...]) -> sl.LayoutNode[sl.ComponentsV2Target]:
    lines: list[sl.TextLike] = []
    for entry in entries:
        if isinstance(entry, Paragraph):
            lines.append(entry.content)
        else:
            lines.extend(Text(t"{field.label}: {field.value}") for field in entry.fields)
    return sl.fallback(sl.stack(*entries), sl.paragraph(Text.join("\n\n", lines)))

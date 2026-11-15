"""Build semantic layout nodes using shorthand suited to Component.render methods.

These factories construct the public records in squid_ui.semantic. Containers accept
layout nodes, turn bare strings and t-strings into paragraphs, and omit None or False
children. For example, ``sl.stack("Details", show_note and sl.note("Draft"))`` includes
the note only when show_note is true.

Factories follow reading order: a semantic identity such as a heading, summary, item label,
table columns, or form label comes before the content it introduces. Runtime identity and
configuration remain keyword-only. ``key`` is required exactly where the runtime reads it
back — on nodes that own session state or custom ids. Records whose key no target reads today
(`Field`, `Column`, `TableRow`, `MediaItem`, `ListItem`) default it to ``""`` rather than
making authors invent identities that nothing consumes.

Collections are unpacked by the caller (``sl.fields(*(sl.field(k, v) for k, v in rows))``).
Factories do not flatten a list argument: explicit unpacking keeps a stray dictionary
or generator from being silently treated as content.
"""

from squid_ui.factories._inputs import (
    ChildLike,
    Conditional,
    TextValue,
)
from squid_ui.factories._inputs import (
    is_builtin_layout_node as is_builtin_layout_node,
)
from squid_ui.factories._inputs import (
    is_layout_node as is_layout_node,
)
from squid_ui.factories._inputs import (
    is_portable_node as is_portable_node,
)
from squid_ui.factories.collections import (
    bullet,
    bullets,
    column,
    columns,
    field,
    fields,
    grid,
    item,
    item_label,
    items,
    media,
    roster,
    table,
    table_row,
    tally,
)
from squid_ui.factories.containers import (
    article,
    aside,
    block,
    cluster,
    details,
    group,
    region,
    section,
    stack,
    summary,
    themed,
)
from squid_ui.factories.content import (
    code,
    figure,
    heading,
    media_item,
    metric,
    note,
    paragraph,
    progress,
    quote,
    status,
    timestamp,
    zoned_timestamp,
)
from squid_ui.factories.controls import (
    action_control,
    action_controls,
    choice,
    choices,
    control_group,
    controlled,
    download,
    entities,
    entity_choice,
    form,
    link,
    nav_option,
    navigation,
    rating,
    routed_action_control,
    routed_choices,
    toggle,
    uncontrolled,
)

__all__ = [
    "ChildLike",
    "Conditional",
    "TextValue",
    "action_control",
    "action_controls",
    "article",
    "aside",
    "block",
    "bullet",
    "bullets",
    "choice",
    "choices",
    "cluster",
    "code",
    "column",
    "columns",
    "control_group",
    "controlled",
    "details",
    "download",
    "entities",
    "entity_choice",
    "field",
    "fields",
    "figure",
    "form",
    "grid",
    "group",
    "heading",
    "item",
    "item_label",
    "items",
    "link",
    "media",
    "media_item",
    "metric",
    "nav_option",
    "navigation",
    "note",
    "paragraph",
    "progress",
    "quote",
    "rating",
    "region",
    "roster",
    "routed_action_control",
    "routed_choices",
    "section",
    "stack",
    "status",
    "summary",
    "table",
    "table_row",
    "tally",
    "themed",
    "timestamp",
    "toggle",
    "uncontrolled",
    "zoned_timestamp",
]

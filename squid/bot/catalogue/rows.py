"""One row format for builds and records in catalogue lists and search results.

Rows are plain text so that bold stays free for search highlighting: a search result
is the same row as its catalogue entry, with the matched words in bold.
"""

import squid_ui as sl
from squid_i18n import Text

type Dimensions = tuple[int | None, int | None, int | None]


def build_row(build_id: int | str, title: sl.TextLike, dimensions: Dimensions) -> Text:
    """Return `#7 Title — 3 x 4 x 5`, leaving out the size when no dimension is known."""
    build_id = str(build_id)  # An ID is not a quantity; keep locale digit grouping out of it.
    if all(value is None for value in dimensions):
        return Text(t"#{build_id} {title}")
    size = " x ".join(str(value) if value is not None else "?" for value in dimensions)
    return Text(t"#{build_id} {title} — {size}")


def record_row(title: sl.TextLike, version_scope: str) -> Text:
    """Return `Title · Subtitle — all time` for a record's one-line title and version scope."""
    scope = version_scope.replace("_", " ")
    return Text(t"{title} — {scope}")

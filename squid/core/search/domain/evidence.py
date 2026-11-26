"""What a search document contains, and which of its values made it match.

A document is a list of field values. The explainer evaluates a parsed query
against those values the same way the database does, and reports each value
that satisfied a clause, with the character ranges of the matching terms.
Results render this evidence instead of guessing what matched.
"""

from dataclasses import dataclass
from decimal import Decimal

from whenever import Instant


@dataclass(frozen=True, slots=True)
class SearchText:
    """Display text with the character ranges to highlight, as half-open Python offsets."""

    text: str
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One value of a field, keyed by the field's storage name.

    Record holder values name the holder's build, so clauses such as `volume<20`
    can be matched against a single holder.
    """

    field: str
    value: str | Decimal | Instant | bool
    holder_build_id: int | None = None


@dataclass(frozen=True, slots=True)
class FieldMatch:
    """A value that satisfied part of the query.

    `spans` are half-open character ranges of the matching terms in `text`; a keyword
    match spans the whole value, and a number or date has none. `close` marks a value
    that matched only through a close spelling of a query word.
    """

    field: str
    text: str
    spans: tuple[tuple[int, int], ...] = ()
    holder_build_id: int | None = None
    close: bool = False


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    """The values that made a hit match, in query order. Empty for a query without clauses."""

    matches: tuple[FieldMatch, ...] = ()

    @property
    def close(self) -> bool:
        """Whether any value matched only through a close spelling."""
        return any(match.close for match in self.matches)

    def highlight(self, text: str, *fields: str) -> SearchText:
        """Mark the matched terms of `text`, taken from matches on any of `fields` with that exact text."""
        spans = sorted(
            span for match in self.matches if match.field in fields and match.text == text for span in match.spans
        )
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return SearchText(text, tuple(merged))

    def outside(self, *fields: str) -> tuple[FieldMatch, ...]:
        """Return the matches on fields other than these, such as a holder's build title."""
        return tuple(match for match in self.matches if match.field not in fields)

"""Category implication and subset generation."""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import combinations

from squid.core.errors import ValidationError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class CategorySemantics:
    """Implications and incompatibilities between canonical tag IDs."""

    implications: Mapping[str, frozenset[str]]
    incompatibilities: Mapping[str, frozenset[str]]

    def closure(self, tags: Iterable[str]) -> frozenset[str]:
        """Return the transitive implication closure for a tag collection."""
        result = set(tags)
        pending = list(result)
        while pending:
            tag = pending.pop()
            for implied in self.implications.get(tag, ()):
                if implied not in result:
                    result.add(implied)
                    pending.append(implied)
        return frozenset(result)

    def is_valid(self, tags: Iterable[str]) -> bool:
        """Return whether the tags and their implications contain no incompatible pair."""
        closed = self.closure(tags)
        return all(not (self.incompatibilities.get(tag, frozenset()) & closed) for tag in closed)


def generate_category_subsets(
    tags: Iterable[str],
    semantics: CategorySemantics,
    *,
    max_size: int = 8,
) -> Iterator[frozenset[str]]:
    """Yield valid subsets of the input tags and their implications, including the empty set.

    Subsets are ordered by size, then sorted tag IDs. Each contains at most
    `max_size` tags; implied tags participate in validation but need not appear
    in the yielded subset. For example, if A implies B, {A} and {B} are separate
    categories. A negative size limit raises `ValidationError` when iteration starts.
    """
    if max_size < 0:
        msg = tr(t"records-maximum-category-size = Maximum category size cannot be negative.")
        raise ValidationError(msg)

    canonical_tags = tuple(sorted(semantics.closure(tags)))
    upper_bound = min(max_size, len(canonical_tags))
    for size in range(upper_bound + 1):
        for subset in combinations(canonical_tags, size):
            category = frozenset(subset)
            if semantics.is_valid(category):
                yield category

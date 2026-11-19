"""Category implication and subset generation."""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import combinations

from squid.core.errors import ValidationError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class CategorySemantics:
    """Implications and incompatibilities between canonical facet IDs."""

    implications: Mapping[str, frozenset[str]]
    incompatibilities: Mapping[str, frozenset[str]]

    def closure(self, facets: Iterable[str]) -> frozenset[str]:
        """Return the transitive implication closure for a facet collection."""
        result = set(facets)
        pending = list(result)
        while pending:
            facet = pending.pop()
            for implied in self.implications.get(facet, ()):
                if implied not in result:
                    result.add(implied)
                    pending.append(implied)
        return frozenset(result)

    def is_valid(self, facets: Iterable[str]) -> bool:
        """Return whether the facets and their implications contain no incompatible pair."""
        closed = self.closure(facets)
        return all(not (self.incompatibilities.get(facet, frozenset()) & closed) for facet in closed)


def generate_category_subsets(
    facets: Iterable[str],
    semantics: CategorySemantics,
    *,
    max_size: int = 8,
) -> Iterator[frozenset[str]]:
    """Yield valid subsets of the input facets and their implications, including the empty set.

    Subsets are ordered by size, then sorted facet IDs. Each contains at most
    `max_size` facets; implied facets participate in validation but need not appear
    in the yielded subset. For example, if A implies B, {A} and {B} are separate
    categories. A negative size limit raises `ValidationError` when iteration starts.
    """
    if max_size < 0:
        msg = tr(t"records-maximum-category-size = Maximum category size cannot be negative.")
        raise ValidationError(msg)

    canonical_facets = tuple(sorted(semantics.closure(facets)))
    upper_bound = min(max_size, len(canonical_facets))
    for size in range(upper_bound + 1):
        for subset in combinations(canonical_facets, size):
            category = frozenset(subset)
            if semantics.is_valid(category):
                yield category

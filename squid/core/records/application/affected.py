"""Find the categories a set of build changes can affect.

A record depends only on its category's members. A change to build *b* can
therefore alter the categories *b* belongs to now, and those it belonged to
before. The membership index supplies the latter.

Two kinds of category can change without *b* in them. Thresholds such as
"at most 4 wide" come from values that builds in the scope actually have, and
families project onto the fixed sizes that fixed doors make concrete. When *b*
adds or removes the only source of a threshold or a size, every category at
that threshold or size appears or disappears, at any size and with any other
restrictions, including ones *b* is not in.

To notice that, compare what is observed now with what was observed before.
"Before" is what the unchanged builds contribute plus every threshold and size
in a live category key: a published category exists only while its threshold
and size are observed. A threshold that only *b* had and no live category
used has nothing to retire, so leaving it out of "before" at worst recomputes
categories that come out unchanged.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from squid.core.records.application.models import CategoryCandidates, CategoryIdentity, PriorPublication

type Threshold = tuple[int, str]
"""A restriction tag ID and a serialized value some build in the scope has."""


@dataclass(frozen=True, slots=True)
class ScopeCategories:
    """Every category in one version scope, and where its thresholds and sizes came from.

    `thresholds` maps each build that contributes thresholds to the values it
    contributes. Families contribute only when they project onto some size.
    `base_keys` maps each fixed build to the base key it makes concrete.
    """

    version_id: int | None
    categories: Mapping[str, CategoryCandidates]
    thresholds: Mapping[int, frozenset[Threshold]]
    base_keys: Mapping[int, str]
    requested_bases: frozenset[str]

    def affected(self, changed: frozenset[int], prior: PriorPublication) -> frozenset[str]:
        """Return the category keys in this scope that changes to `changed` can affect.

        The result can include keys that no longer exist; their records retire.
        """
        if not changed:
            return frozenset()
        live = {key for version, key in prior.live if version == self.version_id}
        affected = {
            key
            for build_id in changed
            for version, key in prior.memberships.get(build_id, ())
            if version == self.version_id
        }
        affected.update(
            key
            for key, category in self.categories.items()
            if any(candidate.build_id in changed for candidate in category.candidates)
        )
        live_identities = tuple(identity for key in live if (identity := CategoryIdentity.from_key(key)) is not None)

        thresholds_now = _union(self.thresholds.values())
        thresholds_before = _union(values for build_id, values in self.thresholds.items() if build_id not in changed)
        thresholds_before |= {
            _threshold(value) for identity in live_identities for value in identity.restriction_values
        }
        touched_thresholds = thresholds_now ^ thresholds_before

        bases_now = set(self.base_keys.values()) | self.requested_bases
        bases_before = {key for build_id, key in self.base_keys.items() if build_id not in changed}
        bases_before |= self.requested_bases | {identity.base_key for identity in live_identities}
        touched_bases = bases_now ^ bases_before

        if touched_thresholds or touched_bases:
            for key in self.categories.keys() | live:
                identity = CategoryIdentity.from_key(key)
                if identity is not None and (
                    identity.base_key in touched_bases
                    or any(_threshold(value) in touched_thresholds for value in identity.restriction_values)
                ):
                    affected.add(key)
        return frozenset(affected)


def _threshold(value: tuple[int, str, str]) -> Threshold:
    tag_id, _operator, serialized = value
    return tag_id, serialized


def _union[T](sets: Iterable[frozenset[T]]) -> set[T]:
    result: set[T] = set()
    for values in sets:
        result |= values
    return result

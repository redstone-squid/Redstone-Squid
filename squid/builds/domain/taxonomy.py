"""Restriction and pattern names that matched no approved official tag.

Submitters, editors, and inference name restrictions and patterns in free text. Saving a build
resolves those names into tag assignments (`squid.builds.application.taxonomy`). A name that
matches no approved official tag, or matches more than one, cannot become an assignment. The
build keeps it as an unresolved name instead of dropping it, so a bad inference or an unknown
term stays visible to reviewers and in titles.

Each save retries the build's unresolved names, so a name stops being unresolved once an official
tag for it exists. Nothing else removes them.

Unresolved names are stored in the build's `extra_info` JSON under `unknown_restrictions` (names
grouped by restriction field) and `unknown_patterns`. `UnresolvedTaxonomy` is the only code that
reads or writes those keys.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Self, cast, get_args

from squid.core.errors import DataIntegrityError

from .models import Info, UnknownRestrictions

type RestrictionField = Literal[
    "wiring_placement_restrictions",
    "animated_restrictions",
    "component_restrictions",
    "miscellaneous_restrictions",
]
"""A build's restriction list, which also groups its unresolved restriction names."""

RESTRICTION_FIELDS: tuple[RestrictionField, ...] = get_args(RestrictionField.__value__)


def normalize_tag_name(value: str) -> str:
    """Collapse case and internal whitespace the way taxonomy resolution matches names."""
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class UnresolvedTaxonomy:
    """A build's unresolved restriction and pattern names, in the order they were first seen.

    Names are unique by `normalize_tag_name`: adding "Hidden  Wiring" to a set holding "hidden
    wiring" keeps the existing spelling. A restriction name belongs to one field; the first field
    it was seen under wins.
    """

    restrictions: tuple[tuple[RestrictionField, str], ...] = ()
    patterns: tuple[str, ...] = ()

    @classmethod
    def read(cls, info: Info) -> Self:
        """Decode the unresolved names stored in a build's `extra_info`.

        Raises:
            DataIntegrityError: The stored value is not a mapping of known restriction fields to
                lists of strings, or the patterns are not a list of strings.
        """
        stored_restrictions: object = info.get("unknown_restrictions", {})
        stored_patterns: object = info.get("unknown_patterns", [])
        if not isinstance(stored_restrictions, dict) or not set(stored_restrictions) <= set(RESTRICTION_FIELDS):
            msg = "Unresolved restriction names are not grouped by restriction field."
            raise DataIntegrityError(
                msg, context={"key": "unknown_restrictions", "stored_type": type(stored_restrictions).__name__}
            )
        restrictions: list[tuple[RestrictionField, str]] = []
        for field_name, names in cast(dict[RestrictionField, object], stored_restrictions).items():
            restrictions.extend((field_name, name) for name in _strings(names, "unknown_restrictions"))
        return cls().add(restrictions=restrictions, patterns=_strings(stored_patterns, "unknown_patterns"))

    def write(self, info: Info) -> None:
        """Store these names in `info`, removing each key that would be empty."""
        restrictions: UnknownRestrictions = {}
        for field_name in RESTRICTION_FIELDS:
            if names := self.restriction_names(field_name):
                restrictions[field_name] = list(names)
        if restrictions:
            info["unknown_restrictions"] = restrictions
        else:
            info.pop("unknown_restrictions", None)
        if self.patterns:
            info["unknown_patterns"] = list(self.patterns)
        else:
            info.pop("unknown_patterns", None)

    def add(
        self,
        *,
        restrictions: Iterable[tuple[RestrictionField, str]] = (),
        patterns: Iterable[str] = (),
    ) -> Self:
        """Return these names followed by any new ones; names already present keep their place."""
        return type(self)(
            restrictions=tuple(_unique([*self.restrictions, *restrictions], key=lambda item: item[1])),
            patterns=tuple(_unique([*self.patterns, *patterns], key=lambda name: name)),
        )

    def retain(self, *, restrictions: frozenset[str], patterns: frozenset[str]) -> Self:
        """Keep only the names whose normalized form appears in the given sets."""
        return type(self)(
            restrictions=tuple(item for item in self.restrictions if normalize_tag_name(item[1]) in restrictions),
            patterns=tuple(name for name in self.patterns if normalize_tag_name(name) in patterns),
        )

    def restriction_names(self, field_name: RestrictionField | None = None) -> tuple[str, ...]:
        """Return the unresolved restriction names in one field, or in every field when it is None."""
        return tuple(name for owner, name in self.restrictions if field_name is None or owner == field_name)


def _strings(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = "Unresolved taxonomy names must be a list of strings."
        raise DataIntegrityError(msg, context={"key": key, "stored_type": type(value).__name__})
    return cast(list[str], value)


def _unique[T](items: Iterable[T], *, key: Callable[[T], str]) -> list[T]:
    seen: set[str] = set()
    unique: list[T] = []
    for item in items:
        normalized = normalize_tag_name(key(item))
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(item)
    return unique

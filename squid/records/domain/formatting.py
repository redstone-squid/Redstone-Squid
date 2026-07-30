"""Versionable record-title formatting abstractions."""

from dataclasses import dataclass
from typing import Protocol

from squid.records.domain.models import RecordClass


@dataclass(frozen=True, slots=True)
class CategoryText:
    """Rendered category text split into the rules' title and subtitle."""

    title: str
    subtitle: str | None = None


@dataclass(frozen=True, slots=True)
class DoorCategory:
    """Facts used by the piston-door title grammar."""

    wiring_restrictions: tuple[str, ...]
    animated_restrictions: tuple[str, ...]
    size: str
    types: tuple[str, ...]
    orientation: str
    component_restrictions: tuple[str, ...] = ()
    miscellaneous_restrictions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtenderCategory:
    """Facts used by the piston-extender title grammar."""

    wiring_restrictions: tuple[str, ...]
    orientation: str
    length: int
    types: tuple[str, ...]
    component_restrictions: tuple[str, ...] = ()
    miscellaneous_restrictions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.length <= 0:
            msg = "Piston extender length must be positive."
            raise ValueError(msg)


class TitleFormatter(Protocol):
    """A ruleset-specific formatter used when snapshotting record results."""

    def format_door(self, category: DoorCategory) -> CategoryText:
        """Format a piston-door category."""
        ...

    def format_extender(self, category: ExtenderCategory) -> CategoryText:
        """Format a piston-extender category."""
        ...

    def format_record(self, record_class: RecordClass, category: CategoryText) -> CategoryText:
        """Prefix a category with its record class."""
        ...


class RulesTitleFormatter:
    """Formatter for the initial Door Rules title grammar."""

    def format_door(self, category: DoorCategory) -> CategoryText:
        title = _join(
            category.wiring_restrictions,
            category.animated_restrictions,
            (category.size,),
            category.types,
            (category.orientation,),
        )
        return CategoryText(
            title=title,
            subtitle=_optional_join(category.component_restrictions, category.miscellaneous_restrictions),
        )

    def format_extender(self, category: ExtenderCategory) -> CategoryText:
        title = _join(
            category.wiring_restrictions,
            (category.orientation, str(category.length)),
            category.types,
            ("piston extender",),
        )
        return CategoryText(
            title=title,
            subtitle=_optional_join(category.component_restrictions, category.miscellaneous_restrictions),
        )

    def format_record(self, record_class: RecordClass, category: CategoryText) -> CategoryText:
        record_name = record_class.value.replace("_", " ").upper()
        return CategoryText(title=f"{record_name} {category.title}", subtitle=category.subtitle)


def _join(*groups: tuple[str, ...]) -> str:
    return " ".join(part.strip() for group in groups for part in group if part.strip())


def _optional_join(*groups: tuple[str, ...]) -> str | None:
    joined = _join(*groups)
    return joined or None

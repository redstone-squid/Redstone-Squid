"""Scoped layout facts, with explicit unknown answers for incomplete content."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Never, Protocol

from squid_ui.errors import LayoutError
from squid_ui.selectors import Match, Query, Selector

if TYPE_CHECKING:
    from squid_ui.planning.composition import OccurrenceId


class UnknownReason(StrEnum):
    UNLOADED = "unloaded"
    UNSUPPORTED = "unsupported"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class Known[T]:
    value: T

    def require(self) -> T:
        return self.value


@dataclass(frozen=True, slots=True)
class Unknown:
    reason: UnknownReason
    detail: str

    def require(self) -> Never:
        raise FactUnavailableError(self)


type Fact[T] = Known[T] | Unknown


class FactUnavailableError(LayoutError):
    """A required contextual read could not be established in its captured scope."""

    def __init__(self, fact: Unknown) -> None:
        self.fact = fact
        super().__init__(f"{fact.reason}: {fact.detail}")


@dataclass(frozen=True, slots=True)
class RegionRef:
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ElementRef[T]:
    kind: type[T]
    keys: tuple[str, ...]


def region_ref(*keys: str) -> RegionRef:
    if not keys or any(not key for key in keys):
        message = "a region reference needs nonempty keys"
        raise ValueError(message)
    return RegionRef(keys)


def element_ref[T](kind: type[T], *keys: str) -> ElementRef[T]:
    if not keys or any(not key for key in keys):
        message = "an element reference needs nonempty keys"
        raise ValueError(message)
    return ElementRef(kind, keys)


@dataclass(frozen=True, slots=True)
class ContentAddress:
    """A flow-relative content anchor, optionally continuing inside that content.

    revision validates the captured data/layout inputs; ordinal page numbers are
    deliberately absent. A continuation boundary is owned by the content protocol.
    """

    flow: RegionRef
    anchor: tuple[str, ...]
    continuation: tuple[int, int] | None = None
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class PageCoordinate:
    region: OccurrenceId
    index: Fact[int]
    address: ContentAddress | None = None

    @property
    def number(self) -> Fact[int]:
        return Known(self.index.value + 1) if isinstance(self.index, Known) else self.index


@dataclass(frozen=True, slots=True)
class PageAddress:
    coordinates: tuple[PageCoordinate, ...]


@dataclass(frozen=True, slots=True)
class FragmentLocation:
    occurrence: OccurrenceId
    page: PageAddress
    fragment: int
    repeated: bool
    visible: bool


@dataclass(frozen=True, slots=True)
class Locations:
    """Known fragments; complete=False leaves unexamined content unclaimed."""

    fragments: tuple[FragmentLocation, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class RegionFacts:
    identity: OccurrenceId
    _count: Fact[int]
    _selection: Fact[PageCoordinate | None]
    _groups: Fact[int]
    _observe: Callable[[str, object], None] | None = None

    def _read[T](self, name: str, value: T) -> T:
        if self._observe is not None:
            self._observe(name, value)
        return value

    @property
    def count(self) -> Fact[int]:
        """Rendered page total, when established for this capture."""
        return self._read("count", self._count)

    @property
    def selection(self) -> Fact[PageCoordinate | None]:
        return self._read("selection", self._selection)

    @property
    def groups(self) -> Fact[int]:
        """Authored group count, independent of the evidence that those groups fit."""
        return self._read("groups", self._groups)


class ScopeQuery(Protocol):
    def __call__[T](self, selector: Selector[T]) -> Query[T]: ...


class LocationQuery(Protocol):
    def __call__[T](self, target: ElementRef[T] | Match[T], within: RegionRef | None) -> Locations: ...


@dataclass(frozen=True, slots=True)
class DocumentScope:
    _query: ScopeQuery

    def query[T](self, selector: Selector[T]) -> Query[T]:
        return self._query(selector)


class LayoutContext:
    """One contextual occurrence's immutable candidate facts and recorded reads."""

    def __init__(
        self,
        *,
        authored: DocumentScope,
        placed: DocumentScope,
        visible: DocumentScope,
        regions: Mapping[RegionRef, RegionFacts],
        locate: LocationQuery,
        containing: OccurrenceId | None = None,
    ) -> None:
        self.authored = authored
        self.placed = placed
        self.visible = visible
        self._regions = regions
        self._locate = locate
        self._containing = containing
        self.observations: list[tuple[object, object]] = []

    def find_region(self, reference: RegionRef) -> RegionFacts | None:
        found = [facts for key, facts in self._regions.items() if key.keys[-len(reference.keys) :] == reference.keys]
        if len(found) > 1:
            message = f"ambiguous region reference: {reference.keys!r}"
            raise LayoutError(message)
        value = found[0] if found else None
        self.observations.append((("region", reference.keys), value is not None))
        if value is None:
            return None
        return replace(value, _observe=lambda name, answer: self.observations.append(((reference.keys, name), answer)))

    def region(self, reference: RegionRef | None = None) -> RegionFacts:
        if reference is None:
            reference = next((key for key, value in self._regions.items() if value.identity == self._containing), None)
        found = self.find_region(reference) if reference is not None else None
        if found is None:
            Unknown(
                UnknownReason.INCOMPLETE, f"required region {reference or 'containing region'} is unavailable"
            ).require()
        return found

    def locate[T](self, target: ElementRef[T] | Match[T], *, within: RegionRef | None = None) -> Locations:
        result = self._locate(target, within)
        self.observations.append((("locate", target, within), result))
        return result

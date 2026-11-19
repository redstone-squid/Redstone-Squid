"""Scoped layout facts, with explicit unknown answers for incomplete content."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Never, Protocol

from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.errors import LayoutError

if TYPE_CHECKING:
    from squid_ui.planning.composition import OccurrenceId
    from squid_ui.planning.pagination.navigation import NavigationContext
    from squid_ui.selectors import Match, Query, Selector


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
    """Refer to a region by its path of authored keys."""
    if not keys or any(not key for key in keys):
        message = "a region reference needs nonempty keys"
        raise ValueError(message)
    return RegionRef(keys)


def element_ref[T](kind: type[T], *keys: str) -> ElementRef[T]:
    """Refer to an authored element by its type and key path."""
    if not keys or any(not key for key in keys):
        message = "an element reference needs nonempty keys"
        raise ValueError(message)
    return ElementRef(kind, keys)


@dataclass(frozen=True, slots=True)
class ContentAddress:
    """A captured flow-relative anchor, not a persisted navigation checkpoint."""

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
    _visible: bool
    _observe: Callable[[str, object], None] | None = field(default=None, compare=False, repr=False)

    @property
    def visible(self) -> bool:
        if self._observe is not None:
            self._observe("visibility", self._visible)
        return self._visible


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
    _navigation: NavigationContext | None = field(default=None, kw_only=True)
    _observe: Callable[[str, object], None] | None = field(default=None, compare=False, repr=False)

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
    def navigation(self) -> NavigationContext | None:
        """Available movements and their bindings for this captured selection."""
        return self._read("navigation", self._navigation)

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
    _size: int = 0

    def query[T](self, selector: Selector[T]) -> Query[T]:
        return self._query(selector)


@dataclass(frozen=True, slots=True)
class _ObservedScope:
    reader: ScopeQuery
    observations: list[tuple[object, object]]
    name: str
    consume: Callable[[int], None] | None
    size: int

    def __call__[T](self, selector: Selector[T]) -> Query[T]:
        result = self.reader(selector)
        matches = result.all()
        if self.consume is not None:
            self.consume(1 + self.size)
        self.observations.append((("query", self.name, selector.description), tuple((m.path, m.node) for m in matches)))
        return result


@dataclass(frozen=True, slots=True)
class _PartialLocations:
    reader: LocationQuery

    def __call__[T](self, target: ElementRef[T] | Match[T], within: RegionRef | None) -> Locations:
        result = self.reader(target, within)
        return Locations(tuple(loc for loc in result.fragments if loc._visible), complete=False)


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
        capabilities: frozenset[str] = frozenset(),
        localization: Localization = NEUTRAL,
        invocations: tuple[tuple[str, OccurrenceId | None, bool], ...] = (),
    ) -> None:
        self._invocations = invocations
        self.capabilities = capabilities
        self.localization = localization
        self.observations: list[tuple[object, object]] = []
        self._scopes = (authored, placed, visible)
        self._consume: Callable[[int], None] | None = None
        self.authored = DocumentScope(
            _ObservedScope(authored._query, self.observations, "authored", None, authored._size), authored._size
        )
        self.placed = DocumentScope(
            _ObservedScope(placed._query, self.observations, "placed", None, placed._size), placed._size
        )
        self.visible = DocumentScope(
            _ObservedScope(visible._query, self.observations, "visible", None, visible._size), visible._size
        )
        self._regions = regions
        self._locate = locate
        self._containing = containing
        self._owner = "$"

    def _evaluating(
        self,
        consume: Callable[[int], None] | None,
        *,
        owner: str = "$",
        containing: OccurrenceId | None = None,
        partial: bool = False,
        authored: DocumentScope | None = None,
    ) -> LayoutContext:
        regions = self._regions
        if partial:
            unknown = Unknown(UnknownReason.INCOMPLETE, "other contextual views have not been resolved")
            regions = {ref: replace(facts, _count=unknown) for ref, facts in regions.items()}
        result = LayoutContext(
            authored=authored or self._scopes[0],
            placed=self._scopes[2] if partial else self._scopes[1],
            visible=self._scopes[2],
            regions=regions,
            locate=_PartialLocations(self._locate) if partial else self._locate,
            containing=containing,
            capabilities=self.capabilities,
            localization=self.localization,
            invocations=self._invocations,
        )
        result._consume = consume
        result._owner = owner
        scopes = result._scopes
        result.authored, result.placed, result.visible = tuple(
            DocumentScope(_ObservedScope(scope._query, result.observations, name, consume, scope._size), scope._size)
            for scope, name in zip(scopes, ("authored", "placed", "visible"), strict=True)
        )
        return result

    @property
    def view_dependent(self) -> bool:
        for key, _value in self.observations:
            match key:
                case ("query", "visible", _) | (*_, "selection" | "visibility" | "navigation"):
                    return True
        return False

    def find_region(self, reference: RegionRef) -> RegionFacts | None:
        if self._consume is not None:
            self._consume(1 + len(self._regions))
        keys = reference.keys
        scoped = (*keys[:-1], f"{self._owner}.{keys[-1]}") if self._owner != "$" else keys
        found = [facts for key, facts in self._regions.items() if key.keys[-len(keys) :] == scoped]
        if not found:
            found = [facts for key, facts in self._regions.items() if key.keys[-len(keys) :] == keys]
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
        if self._consume is not None:
            self._consume(1)
        if within is not None:
            identity = self.region(within).identity
            within = next(ref for ref, facts in self._regions.items() if facts.identity == identity)
        result = self._locate(target, within)
        result = replace(
            result,
            fragments=tuple(
                replace(loc, _observe=lambda name, answer: self.observations.append((("location", name), answer)))
                for loc in result.fragments
            ),
        )
        self.observations.append((("locate", target, within), result))
        return result

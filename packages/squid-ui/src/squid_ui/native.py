"""Contracts for target-owned authoring vocabularies."""

from collections.abc import Mapping
from dataclasses import Field, dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from squid_ui.content.text import TextLike
from squid_ui.target_types import Renderable

if TYPE_CHECKING:
    from squid_ui.authoring.presentations import Alternatives
    from squid_ui.overflow import Paginate
    from squid_ui.planning.composition import CandidateComposition, PlanningContext, SemanticOccurrence
    from squid_ui.planning.offers.policy import Offers, OfferScope
    from squid_ui.planning.realizations import Realization
    from squid_ui.planning.semantic_index import SemanticIndexer


@dataclass(frozen=True, slots=True)
class NativeBudget:
    minimum: int
    preferred: int
    stretch: int
    best_effort: bool


@dataclass(frozen=True, slots=True)
class NativeTraits:
    """Facts the shared runtime and planner need without knowing a native node's class."""

    key: str | None = None
    text: TextLike | None = None
    pagination: Paginate | None = None
    budget: NativeBudget | None = None
    interaction: Mapping[str, object] | None = None
    boundary: tuple[object, ...] | None = None
    panel: bool = False
    transparent: bool = False
    unbreakable: bool = False
    keep_with_next: bool = False
    content_axis: bool = False
    live: bool = False
    fallback: Renderable[Any, Any] | None = None

    @classmethod
    def of(cls, value: object) -> NativeTraits:
        return value.semantics.traits(value) if isinstance(value, NativeValue) else cls()


class NativeSemantics(Protocol):
    """A target's explicit participation in shared traversal and allocation."""

    def traits(self, value: NativeValue) -> NativeTraits: ...

    def alternatives(self, value: NativeValue) -> Alternatives[Any, Any] | None: ...

    def index(
        self, value: NativeValue, index: SemanticIndexer[Any, Any], path: tuple[str, ...], parent: Any
    ) -> None: ...

    def offers[R, C](self, value: NativeValue, scope: OfferScope[R, C]) -> Offers[R, C]: ...

    def navigation_candidate[R, C](
        self,
        value: NativeValue,
        occurrence: SemanticOccurrence[R, C],
        context: PlanningContext,
        *,
        disabled_controls: bool = True,
    ) -> CandidateComposition[R, C, Realization]: ...

    def namespace(self, value: NativeValue, prefix: str) -> NativeValue: ...

    def with_pagination(self, value: NativeValue, pagination: Paginate) -> NativeValue: ...

    def same_allocation(self, left: NativeValue, right: NativeValue, comparison: AllocationComparison) -> bool: ...


class AllocationComparison(Protocol):
    rebind_actions: bool

    def same_node(self, left: Any, right: Any) -> bool: ...

    def same_children(self, left: tuple[Any, ...], right: tuple[Any, ...]) -> bool: ...


class NativeValue:
    """A native node or nested part with semantics supplied by its owning target."""

    __slots__ = ()
    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]
    semantics: ClassVar[NativeSemantics]


class NativeRealization:
    """A realization interpreted and certified by the target that supplied it."""

    __slots__ = ()


class NativeNode[RenderTargetT, ContextT = object](NativeValue, Renderable[RenderTargetT, ContextT]):
    """An authored native root, distinct from nested options and other native metadata."""

    __slots__ = ()

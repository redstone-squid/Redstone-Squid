"""One plan's inputs, and the two cache keys derived from them."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypedDict

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.identity import stable_value
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET
from squid_ui.planning.target import Target, TargetIdentity
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.sources import Position
from squid_ui.text import NEUTRAL, Localization


class Identity:
    """Identity comparison that also keeps its value alive while an exact memo does."""

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Identity) and self.value is other.value


class StaticPlanOptions(TypedDict, total=False):
    """The planner inputs a sessionless render accepts.

    Split from :class:`PlanOptions` so that an entry point which plans no session cannot be
    handed one: `render_static` takes this, and rejecting `session` there is the point of it.
    """

    chrome: Chrome
    localization: Localization
    palette: Palette
    strict: bool
    reservation: ResourceCost


class PlanOptions(StaticPlanOptions, total=False):
    """Every planner input except the target, which each entry point defaults differently.

    Spelled as a TypedDict so that a function forwarding planner inputs declares them once
    rather than restating ten parameters and ten forwardings. :class:`PlanRequest` remains the
    value the planner sees; this is only how a caller's keywords reach it.
    """

    positions: Mapping[str, Position] | None
    nav: PlannedNav | None
    session: PresentationState | None
    search_budget: int


@dataclass(frozen=True, slots=True)
class PlanRequest[BodyT: scene.Body, RenderTargetT, AdapterT]:
    """Everything one call to a planner backend is asked to compile against.

    These travel together through every layer of planning -- the public dispatcher, each
    backend, the layout search, and both cache key encodings -- so they are one value rather
    than a keyword bundle re-declared at each boundary. The two caches are deliberately *not*
    part of this: they are per-runtime storage handed in alongside a request, not a property
    of what was asked for.

    Held values are the caller's, unresolved. A backend that reserves resources from its
    target or localizes its chrome does so into locals and passes the results to
    :meth:`cache_context`, because the exact memo keys on what the caller supplied while the
    structural cache keys on what was actually compiled against.
    """

    target: Target[Any, BodyT, RenderTargetT, AdapterT]
    chrome: Chrome = DEFAULT_CHROME
    localization: Localization = NEUTRAL
    palette: Palette = DEFAULT_PALETTE
    strict: bool = False
    reservation: ResourceCost = EMPTY_COST
    positions: Mapping[str, Position] | None = None
    nav: PlannedNav | None = None
    session: PresentationState | None = None
    search_budget: int = DEFAULT_SEARCH_BUDGET
    presentation: PresentationState = field(init=False, compare=False, repr=False)
    """`session`, or the empty state standing in for it -- resolved once, so that a request
    without a session still has one stable presentation identity to key a memo on."""

    def __post_init__(self) -> None:
        if self.search_budget < 1:
            message = "planner search budget must be positive"
            raise ValueError(message)
        object.__setattr__(self, "presentation", self.session if self.session is not None else PresentationState())

    def exact_key(self) -> tuple[object, ...]:
        """Key the exact memo, by identity: the same objects, not merely equal ones.

        The memo holds callbacks, so a hit must mean the caller is replaying one render with
        the very values it planned from. Identity is what says that, and it is far cheaper
        than hashing a chrome and a palette on every refresh.
        """
        return (
            Identity(self.target),
            Identity(self.chrome),
            Identity(self.localization),
            Identity(self.palette),
            self.reservation,
            self.strict,
            Identity(self.positions),
            Identity(self.nav),
            getattr(self.nav, "version", 0),
            self.search_budget,
        )

    def cache_context(self, *, target: TargetIdentity, chrome: Chrome) -> Mapping[str, object]:
        """Key the structural cache, by value, against what was actually compiled against.

        `target` and `chrome` are passed rather than read off `self` because a backend
        reserves and localizes them first; a plan compiled against a reduced budget must not
        be served to one that asked for the full target.
        """
        return {
            "target": target.fingerprint,
            "presentation": stable_value(self.presentation),
            "chrome": (
                chrome.previous,
                chrome.next,
                chrome.back,
                chrome.home,
                chrome.close,
                chrome.cancel,
                chrome.page_footer(1, 2),
                chrome.and_n_more(2),
            ),
            "locale": self.localization.locale,
            "palette": stable_value(self.palette),
            "reservation": stable_value(self.reservation),
            "strict": self.strict,
            "positions": stable_value(self.positions),
            "search_budget": self.search_budget,
            "nav": (
                None
                if self.nav is None
                else (
                    getattr(self.nav, "__module__", ""),
                    getattr(self.nav, "__qualname__", type(self.nav).__qualname__),
                    getattr(self.nav, "version", 0),
                )
            ),
        }


__all__ = ["Identity", "PlanOptions", "PlanRequest", "StaticPlanOptions"]

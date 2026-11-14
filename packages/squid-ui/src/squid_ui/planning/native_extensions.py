"""Contracts for opaque Discord components inside shared composition.

Adapters prepare immutable declarations once during offer discovery. Measurement
must independently validate the native widget's local structure and count its
resources; a standalone legacy cost is insufficient for shared allocation.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.composition import AtomicContract, PlanningContext, SharedUse
from squid_ui.planning.resources import Axis, ResourceCost, ResourceIdentity
from squid_ui.primitives.nodes import RawItem

type NativePlacement = Literal["root", "panel", "row", "accessory"]


@dataclass(frozen=True, slots=True)
class NativeComposition:
    """An opaque widget's cost, attachments, legal slots, and independent validator.

    ``measure`` validates the prepared scene payload and native resource, then
    returns its additive cost. Row and accessory declarations promise one legal
    widget in that slot. Shared resources are declared attachments; the planner
    adds the widget's own renderer resource separately. Native callbacks remain
    on that resource; Squid-managed bindings need typed control offers.
    """

    contract: AtomicContract
    measure: Callable[[scene.Extension, object, PlanningContext], ResourceCost]
    placements: frozenset[NativePlacement] = frozenset({"root", "panel"})
    text: tuple[str, ...] = ()
    resources: tuple[SharedUse[Asset], ...] = ()

    def __post_init__(self) -> None:
        if self.contract.cost.get(Axis.DISPLAY_TEXT) != sum(map(len, self.text)):
            message = "native composition text must match its declared display-text cost"
            raise LayoutInvariantError(message)
        if dict(self.contract.shared_costs) != {use.identity: use.cost for use in self.resources}:
            message = "native composition attachments must match its shared resource contract"
            raise LayoutInvariantError(message)
        if any(
            use.cost != ResourceCost({Axis.ATTACHMENTS: 1})
            or use.identity != ResourceIdentity("attachment", use.value.key)
            for use in self.resources
        ):
            message = "native composition shared resources must be attachments"
            raise LayoutInvariantError(message)


@runtime_checkable
class ComposableExtension[PayloadT](Protocol):
    """Optional adapter participation in shared Discord composition."""

    def compose(self, payload: PayloadT, *, context: PlanningContext) -> RawItem: ...

"""Rendered tree values and the reuse state retained by ComponentRuntime.

Expansion updates the supplied cache collections in place. The runtime owns their
lifetime and reconciles candidate trees only after successful delivery.
"""

from dataclasses import dataclass, field
from typing import Any, Generic

from squid_reactivity.core import Observation
from squid_reactivity.resources import AsyncBinding
from squid_reactivity.topics import Address
from squid_ui.context import ContextKey
from squid_ui.document import Asset
from squid_ui.runtime._tree import _LayoutRoute
from squid_ui.runtime.definition import AnyComponent, ContextT, RenderTargetT
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import RenderTarget


@dataclass(frozen=True, slots=True)
class ComponentTree(Generic[RenderTargetT, ContextT]):  # noqa: UP046 - imported TypeVars preserve contravariance
    """Layout nodes from a render, with the components that produced them.

    Child boundaries have been expanded into layout nodes; these nodes still need
    target planning before delivery. The component paths let ComponentRuntime
    determine which instances entered or left the delivered tree.
    """

    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    components: dict[str, AnyComponent]
    assets: tuple[Asset, ...] = ()
    document_key: str | None = None
    deferred: tuple[AnyComponent, ...] = ()
    """Children deferred for loading. A tree with deferred children is incomplete: do not plan, draw, or commit it."""
    async_bindings: tuple[AsyncBinding, ...] = ()
    """Async resources observed during rendering, including an atomic resource that suspended expansion."""
    observations: tuple[Address, ...] = ()
    """The bus addresses of every shared cell this render read, deduplicated.

    A frontend reconciles its subscriptions against this so a panel follows exactly what it
    currently looks at. Over-subscribing is the safe direction: a staged render that is later
    discarded costs at most one spurious refresh, while a missed subscription is a stale panel
    until someone clicks it.
    """
    _topology: object = field(default_factory=object, compare=False, repr=False)
    _topology_base: object | None = field(default=None, compare=False, repr=False)
    _removed_components: tuple[tuple[str, AnyComponent], ...] = field(default=(), compare=False, repr=False)
    _added_components: tuple[tuple[str, AnyComponent], ...] = field(default=(), compare=False, repr=False)


@dataclass(slots=True)
class _ComponentRender[RenderTargetT: RenderTarget, ContextT = object]:
    """One component's current pure render result and everything that read produced."""

    revision: int
    root: bool
    inherited_context: dict[ContextKey[Any], object]
    child_context: dict[ContextKey[Any], object]
    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    assets: tuple[Asset, ...]
    document_key: str | None
    observation: Observation
    addresses: tuple[Address, ...]
    async_bindings: tuple[AsyncBinding, ...]


@dataclass(slots=True)
class _ExpandedSubtree[RenderTargetT: RenderTarget, ContextT = object]:
    """A complete expansion below one stable component path, before parent namespacing."""

    component: AnyComponent
    inherited_context: dict[ContextKey[Any], object]
    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    components: dict[str, AnyComponent]
    assets: tuple[Asset, ...]
    async_bindings: tuple[AsyncBinding, ...]
    observations: tuple[Address, ...]
    child_splices: dict[str, _NodeSplice]
    topology: object = field(default_factory=object)


@dataclass(frozen=True, slots=True)
class _NodeSplice:
    route: _LayoutRoute
    count: int
    key: str


@dataclass(frozen=True, slots=True)
class _SpliceResult[RenderTargetT: RenderTarget, ContextT = object]:
    subtree: _ExpandedSubtree[RenderTargetT, ContextT]
    base_topology: object
    removed: tuple[tuple[str, AnyComponent], ...]
    added: tuple[tuple[str, AnyComponent], ...]


@dataclass(slots=True)
class IncrementalRender[RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
    """Keep reusable renders and track which components need another render.

    render_cache stores individual component results; subtree_cache stores results
    with their children already expanded. Dirty sets identify which cached results
    need checking or replacement. RenderCache retains these collections across
    renders and lends them to tree expansion for synchronous updates. Standalone callers
    can also construct an independent view.
    """

    render_cache: dict[AnyComponent, _ComponentRender[RenderTargetT, ContextT]] = field(default_factory=dict)
    """Each component's last render snapshot, keyed by instance."""
    dirty: set[AnyComponent] = field(default_factory=set)
    forced: set[AnyComponent] = field(default_factory=set)
    """Dirty components whose dependencies must not be consulted -- re-render regardless."""
    force_all: bool = False
    subtree_cache: dict[str, _ExpandedSubtree[RenderTargetT, ContextT]] = field(default_factory=dict)
    dirty_paths: set[str] = field(default_factory=set)
    component_paths: dict[AnyComponent, str] | None = None
    """Where each component sat last time, for locating a splice. None disables splicing."""

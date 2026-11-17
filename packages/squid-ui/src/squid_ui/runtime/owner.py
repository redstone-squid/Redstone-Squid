"""Frontend-neutral owner of a reactive component tree."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from squid_reactivity import Subscriptions
from squid_reactivity.resources import AsyncBinding
from squid_reactivity.topics import BusAddress, DependencyAddress
from squid_ui._presentation import PresentationCells
from squid_ui.context_keys import ContextKey
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.snapshot import PlanningSnapshot
from squid_ui.presentation_state import PresentationState
from squid_ui.runtime.definition import AnyComponent, Component
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.runtime.preparation import ComponentPreparation
from squid_ui.runtime.render_cache import RenderCache
from squid_ui.runtime.services import ServiceFailure, UIExecutionHost, _ServiceLifecycle
from squid_ui.runtime.tree import ComponentTree
from squid_ui.selectors.source import QueryRoot
from squid_ui.target_types import RenderTarget


@dataclass(slots=True)
class RenderCandidate[RenderTargetT: RenderTarget = RenderTarget]:
    """A prospective tree and its subscriptions, settled once after delivery.

    Obtain one with runtime.stage(tree). Its revision is captured when the tree
    is first staged, so changes during delivery remain pending after commit.
    Superseding or finishing the runtime discards outstanding candidates.
    """

    _runtime: ComponentRuntime[RenderTargetT]
    tree: ComponentTree[RenderTargetT]
    revision: int
    settled: bool = False

    def _require_pending(self) -> None:
        if self.settled or self._runtime._staged is not self:
            message = "render candidate already settled or superseded"
            raise LayoutInvariantError(message)

    def commit(self) -> None:
        """Adopt a delivered tree before running its mount and unmount hooks."""
        self._require_pending()
        if self.tree.deferred or self.tree.suspended:
            message = "a discovery render cannot be committed"
            raise LayoutInvariantError(message)
        self.settled = True
        self._runtime._staged = None
        self._runtime.subscriptions.commit()
        self._runtime._commit_tree(self.tree, rendered_revision=self.revision)

    def discard(self) -> None:
        """Release prospective reads while retaining the delivered tree's reads."""
        self._discard(release=True)

    def _discard(self, *, release: bool) -> None:
        self._require_pending()
        self.settled = True
        self._runtime._staged = None
        self._runtime.subscriptions.discard()
        if release:
            if self._runtime.presentation_cells.retain(self._runtime.components, committed=False):
                self._runtime._cache.require_full_render()
            self._runtime._release_unused_claims(set(self._runtime.components.values()))
        if self._runtime._cached_tree is self.tree:
            self._runtime._cached_tree = None


class ComponentRuntime[RenderTargetT: RenderTarget = RenderTarget]:
    """Track one component tree as its state changes and new layouts are delivered.

    The runtime keeps the last delivered tree separate from a candidate render.
    This lets the frontend attempt planning and delivery without mounting new
    components or stopping old services before the user sees the replacement.

    The frontend drives the lifetime:

    - render produces a candidate tree, reusing unchanged component results.
    - After successful delivery, commit adopts that tree and runs mount/unmount hooks.
    - finish unmounts the tree and requests service cancellation; wait_services waits
      for cleanup to finish. Closure is permanent, even if an unmount hook fails.

    Rendering, staging, claiming components, and changing context require an open runtime.
    Invalidation after closure is ignored so late notifications and cleanup cannot schedule
    another render. Capture any terminal render before calling finish.

    Asynchronous component loading and delivery belong to the frontend. Presentation
    state, such as selected pages, is retained here for subsequent planning calls.
    """

    def __init__(
        self,
        root: Component[RenderTargetT],
        *,
        presentation: PresentationState | None = None,
        on_invalidate: Callable[[], None] | None = None,
        on_reactive_invalidate: Callable[[DependencyAddress], None] | None = None,
        context: Mapping[ContextKey[Any], object] | None = None,
        plan_cache: PlanCache | None = None,
        execution_host: UIExecutionHost | None = None,
        on_service_error: ServiceFailure | None = None,
    ) -> None:
        self.preparation = ComponentPreparation()
        self.queries = QueryRoot()
        self._closed = False
        self.root = root
        self._services = _ServiceLifecycle(execution_host, on_service_error)
        self._hook_errors: list[Exception] = []
        self._claimed: set[AnyComponent] = set()
        self.claim(root)
        self.presentation = presentation if presentation is not None else PresentationState()
        self.on_invalidate = on_invalidate
        self.on_reactive_invalidate = on_reactive_invalidate
        self.subscriptions = Subscriptions(self._reactive_changed)
        self._context = dict(context or {})
        self._context_view = MappingProxyType(self._context)
        self.plan_cache = plan_cache if plan_cache is not None else PlanCache(32)
        self.planning_snapshot = PlanningSnapshot()
        self._components: dict[str, AnyComponent] = {}
        self._components_view = MappingProxyType(self._components)
        self._cache = RenderCache[RenderTargetT]()
        self._cached_tree: ComponentTree[RenderTargetT] | None = None
        self._cached_revision = -1
        self._staged: RenderCandidate[RenderTargetT] | None = None
        self._committed_tree: ComponentTree[RenderTargetT] | None = None
        self.revision = 0
        """Which render inputs the next render would see; a render captures it and `commit`
        compares against it. Not component state alone -- anything a render reads and a later
        commit could invalidate moves it, including a shared cell another owner wrote."""
        self.dirty = True
        """Whether the committed tree is behind the inputs a fresh render would read."""

    @property
    def presentation_cells(self) -> PresentationCells:
        return self.presentation.declared

    @property
    def closed(self) -> bool:
        """Whether finish has begun; service cleanup may still be running."""
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            message = "component runtime is closed"
            raise RuntimeError(message)

    @property
    def context(self) -> Mapping[ContextKey[Any], object]:
        """Live read-only root bindings. Use ``set_context`` to replace a value and invalidate."""
        return self._context_view

    @property
    def components(self) -> Mapping[str, AnyComponent]:
        """Live read-only placements in the committed tree, cleared by ``finish``."""
        return self._components_view

    def _reactive_changed(self, address: DependencyAddress) -> None:
        if self._closed:
            return
        self.invalidate_address(address)
        if self.on_reactive_invalidate is not None:
            self.on_reactive_invalidate(address)

    def claim(self, component: AnyComponent) -> None:
        """Associate a discovered instance with this runtime until its last use ends."""
        self._require_open()
        if component._runtime is not None and component._runtime is not self:
            message = "a component instance cannot belong to multiple runtimes"
            raise LayoutInvariantError(message)
        component._runtime = self
        self._claimed.add(component)

    def _release_unused_claims(self, retained: set[AnyComponent]) -> None:
        retained.add(self.root)
        for component in self._claimed - retained:
            if component._runtime is self:
                component._runtime = None
                component._parent = None
        self._claimed.intersection_update(retained)
        for component in self.preparation.reports.keys() - retained:
            del self.preparation.reports[component]

    def stage(self, tree: ComponentTree[RenderTargetT]) -> RenderCandidate[RenderTargetT]:
        """Return the candidate for this tree, replacing any earlier prospective tree.

        Repeated staging returns the same candidate and preserves its captured
        revision. Loading can replace discovery trees while the delivered tree's
        dependencies remain subscribed throughout.
        """
        self._require_open()
        if self._staged is not None and self._staged.tree is tree:
            return self._staged
        previous_claims = set(self._claimed)
        try:
            for component in (*tree.components.values(), *tree.deferred):
                self.claim(component)
        except BaseException:
            self._release_unused_claims(previous_claims)
            raise
        if self._staged is not None:
            self._staged._discard(release=False)
        self._release_unused_claims(set(self._components.values()) | set(tree.components.values()) | set(tree.deferred))
        self.subscriptions.stage(tree.observations)
        self._staged = RenderCandidate(self, tree, self.revision)
        return self._staged

    def discard(self) -> None:
        """Discard the outstanding candidate, if any."""
        if self._staged is not None:
            self._staged.discard()
        self._cached_tree = None

    def invalidate(self, component: AnyComponent | None = None, *, check_dependencies: bool = False) -> None:
        """Declare the render inputs moved, so anything rendered before now is stale."""
        self._invalidate_components(
            () if component is None else (component,),
            force_all=component is None,
            check_dependencies=check_dependencies,
        )

    def invalidate_addresses(self, addresses: Iterable[BusAddress]) -> None:
        """Invalidate only component snapshots that observed any current bus address."""
        if self._closed:
            return
        requested = set(addresses)
        if not requested:
            return
        active: set[DependencyAddress] = set(self.subscriptions.dependencies)
        if self._committed_tree is not None:
            active.update(self._committed_tree.observations)
        if self._cached_tree is not None:
            active.update(self._cached_tree.observations)
        relevant = active & requested
        if not relevant:
            return

        matched, covered = self._cache.observing(relevant)
        self._invalidate_components(matched, force_all=not covered, check_dependencies=True)

    def invalidate_address(self, address: BusAddress) -> None:
        """Invalidate component snapshots that observed one current bus address."""
        self.invalidate_addresses((address,))

    def _invalidate_components(
        self,
        components: Iterable[AnyComponent],
        *,
        force_all: bool,
        check_dependencies: bool,
    ) -> None:
        """Record one invalidation revision for a component set."""
        if self._closed:
            return
        self.revision += 1
        self.dirty = True
        self._cached_tree = None
        self._cache.invalidate(components, force_all=force_all, check_dependencies=check_dependencies)
        if self.on_invalidate is not None:
            self.on_invalidate()

    def set_context[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Replace one root context value for subsequent renders."""
        self._require_open()
        self._context[key] = value
        self.invalidate()

    def render(
        self,
        *,
        defer: Callable[[AnyComponent], bool] | None = None,
        reuse_committed: bool = False,
        terminal: bool = False,
    ) -> ComponentTree[RenderTargetT]:
        """Render a candidate tree for the frontend to plan and deliver.

        Call commit after successful delivery so lifecycle hooks follow the displayed tree.

        ``defer`` renders for discovery only -- see :func:`render_component_tree`. Such a tree
        is missing subtrees and must never be passed to :meth:`commit`.
        ``terminal=True`` draws a final view while the host drains shutdown. Such a
        tree must not be committed: it can describe existing state, but cannot start
        another service activation on the closing host.
        """
        self._require_open()
        if not self.dirty and not reuse_committed:
            # A direct render is an explicit request to sample inputs outside the reactive graph.
            # Mounted scheduler paths opt into the committed fast path after recording their cause.
            self.revision += 1
            self.dirty = True
            self._cache.require_full_render()
            self._cached_tree = None
        if reuse_committed and self._cached_tree is not None and self._cached_revision == self.revision:
            if not terminal:
                self._validate_execution_host(self._cached_tree)
                self.stage(self._cached_tree)
            return self._cached_tree
        if reuse_committed and not self.dirty and self._committed_tree is not None:
            if not terminal:
                self._validate_execution_host(self._committed_tree)
                self.stage(self._committed_tree)
            return self._committed_tree
        if reuse_committed and self._backdated_to_committed():
            committed = self._committed_tree
            if committed is None:
                message = "backdating succeeded without a committed tree"
                raise AssertionError(message)
            if not terminal:
                self._validate_execution_host(committed)
                self.stage(committed)
            return committed
        try:
            tree = render_component_tree(
                self.root,
                runtime=self,
                context=self._context,
                defer=defer,
                incremental=self._cache.expansion(),
            )
            self._require_open()
            if not terminal:
                self._validate_execution_host(tree)
        except BaseException:
            retained = set(self._components.values())
            if self._staged is not None:
                retained.update(self._staged.tree.components.values())
                retained.update(self._staged.tree.deferred)
            self._release_unused_claims(retained)
            raise
        if tree.suspended:
            # The incomplete pass has no replacement render snapshot for the component
            # that suspended. Retrying must not reuse its old subtree or clear its cause.
            self._cached_tree = None
            return tree
        if reuse_committed and self._committed_tree is not None and tree == self._committed_tree:
            tree = self._committed_tree
        self._cache.rendered()
        if not terminal:
            self.stage(tree)
        if not tree.deferred:
            self._cached_tree = tree
            self._cached_revision = self.revision
        return tree

    def _validate_execution_host(self, tree: ComponentTree[RenderTargetT]) -> None:
        # A host can close between renders, including while a candidate waits for delivery.
        if any(component._owned_services is not None for component in tree.components.values()):
            self._services.validate()

    def _backdated_to_committed(self) -> bool:
        """Whether every attributed invalidation settled to its committed render value."""
        if self._committed_tree is None or not self._cache.can_backdate():
            return False
        self._cache.rendered()
        self._cached_tree = self._committed_tree
        self._cached_revision = self.revision
        return True

    def pending_cached_atomic_resources(self) -> tuple[AsyncBinding[object], ...]:
        """Return atomic bindings that can settle before the next component-tree render.

        This is deliberately narrower than general cache reuse. Every directly moved render
        source must be an atomic binding from that same snapshot, and no dirty ancestor may
        remove or recontextualize the component before it renders again.
        """
        if self._committed_tree is None:
            return ()
        return self._cache.pending_atomic_resources()

    def commit(self, tree: ComponentTree[RenderTargetT]) -> None:
        """Commit a delivered tree using its staged candidate.

        Frontends can retain the candidate returned by stage and call its commit
        directly. Lifecycle errors do not roll back an already delivered tree.
        """
        self.stage(tree).commit()

    def _commit_tree(self, tree: ComponentTree[RenderTargetT], *, rendered_revision: int) -> None:
        """Reconcile lifecycle after the candidate has adopted its subscriptions."""

        if tree is self._committed_tree:
            if self.revision == rendered_revision:
                self._cached_tree = tree
                self._cached_revision = self.revision
            self.dirty = self.revision != rendered_revision
            return

        incremental = self._can_reconcile_incrementally(tree)
        if incremental:
            self._reconcile_incremental(tree)
        else:
            self._reconcile_components(tree)
        self._committed_tree = tree
        self.presentation_cells.retain(tree.components)
        if self.revision == rendered_revision:
            self._cached_tree = tree
            self._cached_revision = self.revision
        if not incremental:
            self._cache.retain(tree.components)
        self.dirty = self.revision != rendered_revision
        self.queries.publish(tree)
        self._raise_hook_errors()

    def _can_reconcile_incrementally(self, tree: ComponentTree[RenderTargetT]) -> bool:
        # Shared or relocated instances need reconciliation by identity across the whole tree.
        return (
            self._committed_tree is not None
            and tree._topology_base is self._committed_tree._topology
            and len(set(self._components.values())) == len(self._components)
            and len(set(tree.components.values())) == len(tree.components)
            and not (
                {component for _, component in tree._removed_components}
                & {component for _, component in tree._added_components}
            )
        )

    def _reconcile_incremental(self, tree: ComponentTree[RenderTargetT]) -> None:
        """Apply placement changes when each instance has one unchanged location."""
        for path, component in sorted(
            tree._removed_components,
            key=lambda item: _component_depth(item[0]),
            reverse=True,
        ):
            self._unmount(component)
            component._runtime = None
            if path != "$":
                component._parent = None
            if self._components.get(path) is component:
                self._components.pop(path)
            self._cache.remove_path(component)
        for path, component in sorted(tree._added_components, key=lambda item: _component_depth(item[0])):
            self._components[path] = component
            self._cache.add_path(component, path)
            self._mount(component, path)
        self._cache.discard_removed(tree._removed_components)
        self._release_unused_claims(set(tree.components.values()))

    def _reconcile_components(self, tree: ComponentTree[RenderTargetT]) -> None:
        """Mount and unmount each changed instance once, including shared placements."""
        old_instances = set(self._components.values())
        new_instances = set(tree.components.values())
        removed = [(path, component) for path, component in self._components.items() if component not in new_instances]
        added = [(path, component) for path, component in tree.components.items() if component not in old_instances]
        visited: set[AnyComponent] = set()
        for _path, component in sorted(removed, key=lambda item: _component_depth(item[0]), reverse=True):
            if component not in visited:
                visited.add(component)
                self._unmount(component)
        visited.clear()
        for path, component in sorted(added, key=lambda item: _component_depth(item[0])):
            if component not in visited:
                visited.add(component)
                self._mount(component, path)
        self._release_unused_claims(new_instances)
        self._components.clear()
        self._components.update(tree.components)

    def _mount(self, component: AnyComponent, path: str) -> None:
        self.claim(component)
        owner = component._owned_services
        try:
            if owner is not None:
                self._services.activate(owner, path)
            component.on_mount()
        except Exception as error:
            if owner is not None:
                self._services.cancel(owner)
            self._hook_errors.append(error)

    def _unmount(self, component: AnyComponent) -> None:
        # Cancel first so services start shutting down even if the unmount hook raises.
        if (owner := component._owned_services) is not None:
            self._services.cancel(owner)
        try:
            component.on_unmount()
        except Exception as error:
            self._hook_errors.append(error)

    def _raise_hook_errors(self) -> None:
        if self._hook_errors:
            errors, self._hook_errors = self._hook_errors, []
            msg = "component lifecycle hooks failed"
            raise ExceptionGroup(msg, errors)

    def check_service_wait(self) -> None:
        """Reject a caller whose own service run would be included in shutdown."""
        self._services.check_wait()

    async def wait_services_ready(self) -> None:
        """Wait for the mounted services to finish setup.

        This covers the runs mounted when the call begins. If setup fails, the error is
        raised and the remaining services keep running. Call this outside delivery locks
        so setup can use the frontend while you wait.
        """
        await self._services.wait_ready()

    async def wait_services(self, *, allow_reentry: bool = False) -> None:
        """Wait for services removed from the tree to finish cleaning up.

        Call this outside delivery locks: cleanup may need to use the frontend too.

        Args:
            allow_reentry: Let a service call this during its own cleanup and return
                immediately. Frontends use this when cleanup triggers another frontend
                operation; the outer caller or host waits for the service to finish.
                The default raises an error if a service tries to wait for itself.
        """
        await self._services.wait(allow_reentry=allow_reentry)

    def finish(self) -> None:
        """Permanently close the runtime and ask its services to stop.

        Repeated calls do nothing, including from unmount hooks. Closure remains final
        if a hook fails. Inspection and service waits remain available; create a new
        runtime to render again. Call ``await runtime.wait_services()`` for cleanup.
        """
        if self._closed:
            return
        self._closed = True
        self.presentation_cells.retain({})
        self.discard()
        self.subscriptions.clear()
        visited: set[AnyComponent] = set()
        for _path, component in sorted(
            self._components.items(),
            key=lambda item: _component_depth(item[0]),
            reverse=True,
        ):
            if component not in visited:
                visited.add(component)
                self._unmount(component)
        for component in self._claimed:
            if component._runtime is self:
                component._runtime = None
                component._parent = None
        self._claimed.clear()
        self.preparation.reports.clear()
        self._components.clear()
        self._cache.clear()
        self._cached_tree = None
        self._committed_tree = None
        self.queries.publish(())
        self.planning_snapshot.clear()
        self.presentation.retained_composition = None
        self.root._runtime = None
        self._raise_hook_errors()


def _component_depth(path: str) -> int:
    return 0 if path == "$" else path.count(".") + 1

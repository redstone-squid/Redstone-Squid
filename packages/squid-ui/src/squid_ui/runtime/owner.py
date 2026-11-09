"""Frontend-neutral owner of a reactive component tree."""

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from squid_reactivity.resources import AsyncBinding
from squid_reactivity.topics import Address
from squid_ui.context import ContextKey
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.presentation import PresentationState
from squid_ui.runtime.definition import AnyComponent, Component
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.runtime.render_cache import RenderCache
from squid_ui.runtime.services import ServiceFailure, UIExecutionHost, _owns_current_activity, _ServiceLifecycle
from squid_ui.runtime.tree import ComponentTree
from squid_ui.target_types import RenderTarget


class ComponentRuntime[RenderTargetT: RenderTarget = RenderTarget]:
    """Track one component tree as its state changes and new layouts are delivered.

    The runtime keeps the last delivered tree separate from a candidate render.
    This lets the frontend attempt planning and delivery without mounting new
    components or stopping old services before the user sees the replacement.

    The frontend drives the lifetime:

    - render produces a candidate tree, reusing unchanged component results.
    - After successful delivery, commit adopts that tree and runs mount/unmount hooks.
    - finish unmounts the tree and requests service cancellation; wait_services waits
      for cleanup to finish.

    Asynchronous component loading and delivery belong to the frontend. Presentation
    state, such as selected pages, is retained here for subsequent planning calls.
    """

    def __init__(
        self,
        root: Component[RenderTargetT],
        *,
        presentation: PresentationState | None = None,
        on_invalidate: Callable[[], None] | None = None,
        context: Mapping[ContextKey[Any], object] | None = None,
        plan_cache: PlanCache | None = None,
        execution_host: UIExecutionHost | None = None,
        on_service_error: ServiceFailure | None = None,
    ) -> None:
        self.root = root
        self._services = _ServiceLifecycle(execution_host, on_service_error)
        self._hook_errors: list[Exception] = []
        root._runtime = self
        self.presentation = presentation if presentation is not None else PresentationState()
        self.on_invalidate = on_invalidate
        self.context = dict(context or {})
        self.plan_cache = plan_cache if plan_cache is not None else PlanCache(32)
        self.plan_memo = PlanMemo()
        self.components: dict[str, AnyComponent] = {}
        self._cache = RenderCache[RenderTargetT]()
        self._candidate_tree: ComponentTree[RenderTargetT] | None = None
        self._candidate_revision = -1
        self._committed_tree: ComponentTree[RenderTargetT] | None = None
        self.revision = 0
        """Which render inputs the next render would see; a render captures it and `commit`
        compares against it. Not component state alone -- anything a render reads and a later
        commit could invalidate moves it, including a shared cell another owner wrote."""
        self.dirty = True
        """Whether the committed tree is behind the inputs a fresh render would read."""

    def invalidate(self, component: AnyComponent | None = None, *, check_dependencies: bool = False) -> None:
        """Declare the render inputs moved, so anything rendered before now is stale."""
        self._invalidate_components(
            () if component is None else (component,),
            force_all=component is None,
            check_dependencies=check_dependencies,
        )

    def invalidate_addresses(self, addresses: Iterable[Address]) -> None:
        """Invalidate only component snapshots that observed any current bus address."""
        requested = set(addresses)
        if not requested:
            return
        active: set[Address] = set()
        if self._committed_tree is not None:
            active.update(self._committed_tree.observations)
        if self._candidate_tree is not None:
            active.update(self._candidate_tree.observations)
        relevant = requested & active
        if not relevant:
            return

        matched, covered = self._cache.observing(relevant)
        self._invalidate_components(matched, force_all=not covered, check_dependencies=True)

    def invalidate_address(self, address: Address) -> None:
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
        self.revision += 1
        self.dirty = True
        self._candidate_tree = None
        self._cache.invalidate(components, force_all=force_all, check_dependencies=check_dependencies)
        if self.on_invalidate is not None:
            self.on_invalidate()

    def set_context[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Replace one root context value for subsequent renders."""
        self.context[key] = value
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
        if not self.dirty and not reuse_committed:
            # A direct render is an explicit request to sample inputs outside the reactive graph.
            # Mounted scheduler paths opt into the committed fast path after recording their cause.
            self.revision += 1
            self.dirty = True
            self._cache.require_full_render()
            self._candidate_tree = None
        if reuse_committed and self._candidate_tree is not None and self._candidate_revision == self.revision:
            if not terminal:
                self._validate_execution_host(self._candidate_tree)
            return self._candidate_tree
        if reuse_committed and not self.dirty and self._committed_tree is not None:
            if not terminal:
                self._validate_execution_host(self._committed_tree)
            return self._committed_tree
        if reuse_committed and self._backdated_to_committed():
            committed = self._committed_tree
            if committed is None:
                message = "backdating succeeded without a committed tree"
                raise AssertionError(message)
            if not terminal:
                self._validate_execution_host(committed)
            return committed
        tree = render_component_tree(
            self.root,
            runtime=self,
            context=self.context,
            defer=defer,
            incremental=self._cache.expansion(),
        )
        if not terminal:
            self._validate_execution_host(tree)
        if reuse_committed and self._committed_tree is not None and tree == self._committed_tree:
            tree = self._committed_tree
        self._cache.rendered()
        if not tree.deferred:
            self._candidate_tree = tree
            self._candidate_revision = self.revision
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
        self._candidate_tree = self._committed_tree
        self._candidate_revision = self.revision
        return True

    def pending_cached_atomic_resources(self) -> tuple[AsyncBinding, ...]:
        """Return atomic bindings that can settle before the next component-tree render.

        This is deliberately narrower than general cache reuse. Every directly moved render
        source must be an atomic binding from that same snapshot, and no dirty ancestor may
        remove or recontextualize the component before it renders again.
        """
        if self._committed_tree is None:
            return ()
        return self._cache.pending_atomic_resources()

    def commit(self, tree: ComponentTree[RenderTargetT], *, rendered_revision: int | None = None) -> None:
        """Reconcile a delivered tree, unmounting children first and mounting parents first.

        Pass the revision captured at render time so changes during delivery leave the
        runtime dirty. Lifecycle errors are collected and raised as an ExceptionGroup
        after reconciliation; they do not roll back an already delivered tree. Discovery
        trees with deferred components cannot be committed.
        """
        if tree.deferred:
            # A discovery tree is missing subtrees; committing one would unmount live
            # components that are only absent because expansion stopped early.
            message = "a discovery render cannot be committed"
            raise LayoutInvariantError(message)

        def depth(path: str) -> int:
            return 0 if path == "$" else path.count(".") + 1

        if tree is self._committed_tree:
            if rendered_revision is None or self.revision == rendered_revision:
                self._candidate_tree = tree
                self._candidate_revision = self.revision
            self.dirty = rendered_revision is not None and self.revision != rendered_revision
            return

        if self._committed_tree is not None and tree._topology_base is self._committed_tree._topology:
            for path, component in sorted(
                tree._removed_components,
                key=lambda item: depth(item[0]),
                reverse=True,
            ):
                self._unmount(component)
                component._runtime = None
                if path != "$":
                    component._parent = None
                if self.components.get(path) is component:
                    self.components.pop(path)
                self._cache.remove_path(component)
            for path, component in sorted(tree._added_components, key=lambda item: depth(item[0])):
                self.components[path] = component
                self._cache.add_path(component, path)
                self._mount(component, path)
            self._cache.discard_removed(tree._removed_components)
            self._committed_tree = tree
            if rendered_revision is None or self.revision == rendered_revision:
                self._candidate_tree = tree
                self._candidate_revision = self.revision
            self.dirty = rendered_revision is not None and self.revision != rendered_revision
            self._raise_hook_errors()
            return

        removed = [
            (path, component)
            for path, component in self.components.items()
            if tree.components.get(path) is not component
        ]
        added = [
            (path, component)
            for path, component in tree.components.items()
            if self.components.get(path) is not component
        ]
        for path, component in sorted(removed, key=lambda item: depth(item[0]), reverse=True):
            self._unmount(component)
            component._runtime = None
            if path != "$":
                component._parent = None
        for path, component in sorted(added, key=lambda item: depth(item[0])):
            self._mount(component, path)
        self.components = dict(tree.components)
        self._committed_tree = tree
        if rendered_revision is None or self.revision == rendered_revision:
            self._candidate_tree = tree
            self._candidate_revision = self.revision
        self._cache.retain(tree.components)
        self.dirty = rendered_revision is not None and self.revision != rendered_revision
        self._raise_hook_errors()

    def _mount(self, component: AnyComponent, path: str) -> None:
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
        owners = self._services._active | self._services._retired
        if _owns_current_activity(
            activation for owner in owners for activation in (*owner._current.values(), *owner._retired)
        ):
            message = "service activity cannot await its own component's shutdown"
            raise RuntimeError(message)

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
        """Unmount the tree and ask its services to stop.

        Call ``await runtime.wait_services()`` afterwards to wait for cleanup.
        """
        for _path, component in sorted(
            self.components.items(),
            key=lambda item: 0 if item[0] == "$" else item[0].count(".") + 1,
            reverse=True,
        ):
            self._unmount(component)
            component._runtime = None
            component._parent = None
        self.components.clear()
        self._cache.clear()
        self._candidate_tree = None
        self._committed_tree = None
        self.plan_memo.clear()
        self.root._runtime = None
        self._raise_hook_errors()

"""Own render snapshots, invalidation causes, and their component-path index.

The component runtime owns one cache for its lifetime. Expansion borrows its mutable
collections for one synchronous render; lifecycle reconciliation prunes them only after
delivery. Pruning must retain invalidations raised by mount and unmount hooks.
"""

from collections.abc import Iterable, Mapping

from squid_reactivity.resources import AsyncBinding, PendingMode
from squid_reactivity.topics import Address
from squid_ui.runtime.async_bindings import async_render_policy
from squid_ui.runtime.definition import AnyComponent
from squid_ui.runtime.tree import IncrementalRender, _ComponentRender, _ExpandedSubtree
from squid_ui.target_types import RenderTarget


class RenderCache[RenderTargetT: RenderTarget]:
    """Retain reusable tree work until reconciliation removes it or the owner finishes."""

    def __init__(self) -> None:
        self._renders: dict[tuple[AnyComponent, str], _ComponentRender[RenderTargetT]] = {}
        self._subtrees: dict[str, _ExpandedSubtree[RenderTargetT]] = {}
        self._dirty: set[AnyComponent] = set()
        self._forced: set[AnyComponent] = set()
        self._dirty_paths: set[str] = set()
        self._paths: dict[AnyComponent, set[str]] = {}
        self._force_all = True

    def expansion(self) -> IncrementalRender[RenderTargetT]:
        """Lend the expansion algorithm a view valid for one synchronous render.

        Expansion may replace snapshots and consume dirty entries during a splice.
        It must not retain this view across lifecycle reconciliation, which can replace
        the backing dictionaries. Failed renders do not clear invalidation causes.
        """
        return IncrementalRender(
            render_cache=self._renders,
            dirty=self._dirty,
            forced=self._forced,
            force_all=self._force_all,
            subtree_cache=self._subtrees,
            dirty_paths=self._dirty_paths,
            component_paths=self._paths,
        )

    def observing(self, addresses: set[Address]) -> tuple[set[AnyComponent], bool]:
        """Find affected snapshots and whether they account for every address."""
        matched: set[AnyComponent] = set()
        covered: set[Address] = set()
        for (component, _path), snapshot in self._renders.items():
            observed = addresses.intersection(snapshot.observation.addresses())
            if observed:
                matched.add(component)
                covered.update(observed)
        return matched, covered == addresses

    def invalidate(
        self,
        components: Iterable[AnyComponent],
        *,
        force_all: bool,
        check_dependencies: bool,
    ) -> None:
        """Attribute invalidations to snapshots and every enclosing subtree.

        Unknown component paths require a full expansion. Explicit invalidations force
        rendering even if observed dependencies still match; dependency invalidations
        may instead be satisfied by the cached observation.
        """
        if force_all:
            self._force_all = True
        for component in components:
            self._dirty.add(component)
            if not check_dependencies:
                self._forced.add(component)
            paths = self._paths.get(component)
            if not paths:
                self._force_all = True
            else:
                for path in paths:
                    self._dirty_paths.add(path)
                    while path != "$":
                        path = "$" if "." not in path else path.rsplit(".", 1)[0]
                        self._dirty_paths.add(path)

    def require_full_render(self) -> None:
        """Ignore reuse when the host explicitly samples nonreactive inputs."""
        self._force_all = True

    def can_backdate(self) -> bool:
        """Whether every attributed change settled back to its cached observation."""
        if self._force_all or self._forced or not self._dirty:
            return False
        for component in self._dirty:
            paths = self._paths.get(component)
            if not paths:
                return False
            for path in paths:
                cached = self._renders.get((component, path))
                if (
                    cached is None
                    or cached.revision != component.__dict__.get("_state_revision", 0)
                    or not cached.observation.current()
                    or cached.observation.addresses() != cached.addresses
                ):
                    return False
        return True

    def pending_atomic_resources(self) -> tuple[AsyncBinding, ...]:
        """Return atomic bindings that can settle before the next component-tree render.

        This is deliberately narrower than general cache reuse. Every directly moved render
        source must be an atomic binding from that same snapshot, and no dirty ancestor may
        remove or recontextualize the component before it renders again.
        """
        if self._force_all or not self._dirty:
            return ()

        # Shared placements may observe different contexts. Let normal expansion settle them.
        if any(len(paths) > 1 for paths in self._paths.values()):
            return ()
        dirty_paths = {
            component: next(iter(paths)) for component in self._dirty if (paths := self._paths.get(component))
        }
        if len(dirty_paths) != len(self._dirty):
            return ()

        found: list[AsyncBinding] = []
        seen: set[int] = set()
        for component, path in dirty_paths.items():
            if component in self._forced:
                continue
            if any(
                ancestor is not component and (ancestor_path == "$" or path.startswith(f"{ancestor_path}."))
                for ancestor, ancestor_path in dirty_paths.items()
            ):
                continue
            cached = self._renders.get((component, path))
            if cached is None or cached.revision != component.__dict__.get("_state_revision", 0):
                continue
            atomic = {
                id(binding): binding
                for binding in cached.async_bindings
                if async_render_policy(binding).pending_mode is PendingMode.ATOMIC
            }
            moved = tuple(
                source for source, version in tuple(cached.observation.sources.items()) if source.settle() != version
            )
            if not moved or any(id(source) not in atomic for source in moved):
                continue
            bindings = tuple(atomic[id(source)] for source in moved)
            if not all(binding.pending for binding in bindings):
                continue
            for binding in bindings:
                identity = id(binding)
                if identity not in seen:
                    seen.add(identity)
                    found.append(binding)
        return tuple(found)

    def rendered(self) -> None:
        """Clear the causes consumed by a completed expansion or a backdated render."""
        self._dirty.clear()
        self._forced.clear()
        self._force_all = False
        self._dirty_paths.clear()

    def remove_path(self, component: AnyComponent) -> None:
        """Forget a removed component after its unmount hook has run."""
        self._paths.pop(component, None)

    def add_path(self, component: AnyComponent, path: str) -> None:
        """Index an incrementally added component before its mount hook runs."""
        self._paths.setdefault(component, set()).add(path)

    def discard_removed(self, removed: Iterable[tuple[str, AnyComponent]]) -> None:
        """Drop removed snapshots without disturbing replacements or hook invalidations."""
        for path, component in removed:
            if component not in self._paths:
                for placement in tuple(self._renders):
                    if placement[0] is component:
                        self._renders.pop(placement)
            cached = self._subtrees.get(path)
            if cached is not None and cached.component is component:
                self._subtrees.pop(path)

    def retain(self, components: Mapping[str, AnyComponent]) -> None:
        """Reconcile a complete delivered topology after lifecycle hooks have run.

        Dirty causes deliberately survive: hooks can change state after the candidate
        was rendered, and the next render must observe those writes.
        """
        self._paths = {}
        for path, component in components.items():
            self._paths.setdefault(component, set()).add(path)
        live = set(components.values())
        self._renders = {
            (component, path): snapshot
            for (component, path), snapshot in self._renders.items()
            if component in live and components.get(path) is component
        }
        self._subtrees = {
            path: snapshot for path, snapshot in self._subtrees.items() if components.get(path) is snapshot.component
        }

        # Hooks can invalidate a shared instance while the old placement index is still active.
        self.invalidate(tuple(self._dirty), force_all=False, check_dependencies=True)

    def clear(self) -> None:
        """Release every snapshot and path when the component runtime finishes."""
        self._renders.clear()
        self._subtrees.clear()
        self._dirty.clear()
        self._forced.clear()
        self._dirty_paths.clear()
        self._paths.clear()

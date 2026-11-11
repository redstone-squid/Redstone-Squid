"""The component authoring contract and its render-local context.

Components describe layout, expose lifecycle hooks, and declare service ownership.
Tree expansion and caching live in the runtime's execution modules.
"""

from abc import ABC, abstractmethod
from contextvars import ContextVar
from itertools import count
from typing import Any, ClassVar, Generic, Protocol, Self, TypeVar, cast, overload, override

from squid_reactivity.core import (
    Reactive,
)
from squid_ui.context import ContextKey
from squid_ui.document import DocumentLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives.nodes import Boundary
from squid_ui.runtime.services import ManagedService, ServiceOwner
from squid_ui.target_types import Renderable, RenderTarget

type AnyComponent = Component[Any, Any]
"""A mounted component for any render target.

`Component`'s `RenderTargetT` defaults, so a bare `Component` annotation means `Component[RenderTarget]`
and rejects every other target -- including `Self` inside `Component`'s own methods. The tree
machinery holds components for whatever target the caller mounted, so it says so, in the
same spirit as `AnyTarget`.
"""


class RuntimeOwner(Protocol):
    def claim(self, component: AnyComponent) -> None: ...

    def invalidate(self, component: AnyComponent | None = None, *, check_dependencies: bool = False) -> None: ...


_CURRENT_CONTEXT: ContextVar[dict[ContextKey[Any], object] | None] = ContextVar(
    "squid_ui_component_context", default=None
)


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


ContextT = TypeVar("ContextT", contravariant=True, default=object)

RenderTargetT = TypeVar("RenderTargetT", contravariant=True, default=RenderTarget)
"""The dialects a component's rendered nodes can be drawn in.

Declared the old way, and contravariant on purpose, in a file that otherwise uses PEP 695.
`RenderTargetT` reaches `Component` through `DocumentLike[RenderTargetT, ContextT]`, which is contravariant
because `Renderable` puts the render target in a parameter position -- and neither pyrefly nor
basedpyright infers variance through that nesting. PEP 695 has no syntax for declaring variance,
so this is the only way to preserve portable-component substitution.
"""


_COMPONENT_KEYS = count()


class Component(Reactive, Renderable[RenderTargetT, ContextT], ABC, Generic[RenderTargetT, ContextT]):
    """A reusable part of an interface, combining state with a render method.

    render describes the current layout without changing state. State changes
    request another render, but planning and delivery belong to the frontend.
    Create and retain children during setup or actions, then return them directly or
    include them in layout containers. Component construction during rendering is forbidden.
    Each child instance has an automatic namespace, stable across reordering under the
    same parent. Use child.keyed("name") for a namespace stable across new instances or
    process restarts. Keys identify placements; they never recover another instance's state.
    Both forms prefix control and presentation keys so children can share local names.
    An instance may have several placements in one runtime, sharing state and lifecycle
    while each placement renders in its own context and keeps its own presentation state.

    A mounted frontend calls on_load before the first render. Once delivery
    succeeds, ComponentRuntime mounts added components and unmounts removed ones;
    owned services run for that mounted lifetime. Calling render_component_tree
    directly only expands the layout and does not run these lifecycle steps.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        if _CURRENT_CONTEXT.get() is not None:
            message = (
                f"{cls.__name__} was constructed during rendering. "
                "Create and retain child components during setup or an action; render only places them."
            )
            raise LayoutInvariantError(message)
        instance = super().__new__(cls, *args, **kwargs)
        vars(instance)["_composition_key"] = f"child-{next(_COMPONENT_KEYS)}"
        return instance

    _composition_key: str
    _runtime: RuntimeOwner | None = None
    _parent: AnyComponent | None = None
    _owned_services: ServiceOwner | None = None
    _loaded: bool = False
    """Whether this instance's :meth:`on_load` has completed. Owned by the frontend."""
    _reactive_internal_attributes = frozenset({"_runtime", "_parent", "_loaded", "_state_revision"})

    @override
    def on_state_rollback(self) -> None:
        vars(self)["_state_revision"] = vars(self).get("_state_revision", 0) + 1

    event_frontend: ClassVar[str | None] = None
    """An explicit dispatch requirement; None leaves the component portable."""

    @abstractmethod
    def render(self) -> DocumentLike[RenderTargetT, ContextT]:
        """Return the current layout synchronously, without changing state."""

    def keyed(self, key: str | int) -> Boundary[RenderTargetT, ContextT]:
        """Place this instance under an explicit control and presentation namespace.

        Return an immutable reference without modifying or cloning the component.
        Keys must be nonempty, contain no dots, and be unique within the parent's
        render. Use distinct keys to place the same instance twice under one parent.
        Replacing an instance at a key mounts the replacement;
        its state is not copied from the previous component.
        """
        return Boundary(self, str(key))

    def boundary(self, child: Component[RenderTargetT, ContextT], *, key: str) -> Boundary[RenderTargetT, ContextT]:
        """Compatibility spelling for child.keyed(key)."""
        return child.keyed(key)

    async def on_load(self) -> None:
        """Fetch what this component cannot render without, before its first render.

        Runs once per instance, before the first delivery that would show it, and before
        :meth:`render` is ever called on it: a frontend stops expanding at an unloaded
        component rather than rendering it empty. Writes are ordinary pre-delivery state, so
        the delivered view is the loaded one -- there is no loading paint to design for.

        A raise leaves the instance eligible to retry on the next delivery attempt, and
        nothing is delivered in the meantime. Data the component can degrade without belongs
        in declared state with a render branch, refreshed by a handler, not here.

        Data that has to stay live belongs in a `sl.resource` instead. Because this runs once
        and under no consumer, its reads are untracked: `sl.watch()` here would follow
        nothing, and there would be no second run to reload it anyway.
        """

    def on_mount(self) -> None:
        """Run when the first placement of this instance enters the delivered tree."""

    def on_unmount(self) -> None:
        """Run when the last placement leaves the delivered tree or the frontend finishes."""

    def own[ServiceT: ManagedService](self, service: ServiceT) -> ServiceT:
        """Give this component a service to run while it's mounted.

        Call this during setup, before the first render. It returns the same instance,
        ready to store on the component and share through ``provide``. The service
        starts after a successful delivery and stops when the component unmounts.

        Remounting uses the same instance and starts a fresh run after cleanup finishes.
        """
        if vars(self).get("_ownership_sealed", False):
            msg = "component service ownership must be declared before rendering"
            raise RuntimeError(msg)
        owner = self._owned_services
        if owner is None:
            owner = ServiceOwner()
            vars(self)["_owned_services"] = owner
        return owner.own(service)

    def invalidate(self) -> None:
        """Mark this component's message as needing a re-render."""
        vars(self)["_state_revision"] = vars(self).get("_state_revision", 0) + 1
        if self._runtime is not None:
            self._runtime.invalidate(self)
        elif self._parent is not None:
            self._parent.invalidate()

    def provide[ValueT](self, key: ContextKey[ValueT], value: ValueT) -> None:
        """Provide a value to descendants for this render.

        Providing a service shares access; it neither owns nor starts the service.
        Descendants can receive it before its run is ready.
        """
        context = _CURRENT_CONTEXT.get()
        if context is None:
            message = "provide() is only available while rendering"
            raise RuntimeError(message)
        context[key] = value

    @overload
    def inject[ValueT](self, key: ContextKey[ValueT]) -> ValueT: ...

    @overload
    def inject[ValueT](self, key: ContextKey[ValueT], default: ValueT) -> ValueT: ...

    def inject[ValueT](self, key: ContextKey[ValueT], default: ValueT | _Missing = _MISSING) -> ValueT:
        """Read the nearest provided value while rendering."""
        context = _CURRENT_CONTEXT.get()
        if context is not None and key in context:
            return cast(ValueT, context[key])
        if not isinstance(default, _Missing):
            return default
        message = f"no value was provided for context key {key.name!r}"
        raise LookupError(message)

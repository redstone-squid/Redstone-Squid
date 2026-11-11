"""Drive widget state machines by semantic key without a frontend.

``mounted`` creates a bare ComponentDriver; ``driving`` wraps a widget's configured
component. For example, ``driving(menu.build_component())`` retains the menu's Close
behavior, while ``mounted(menu)`` tests the transition rules without that finish policy.
``routed`` records the states a stateless render asks the host to encode.

Handlers run directly, without frontend transactions or form validation. These helpers
skip target planning unless `routed` receives a target or contains routed pagination.
Use frontend tests to check native dispatch.

Import this public API from squid_ui_widgets.testing; it is not re-exported at the
package root.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, cast, overload

from squid_ui import scene
from squid_ui import testing as engine
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike, as_document
from squid_ui.planning import AdapterProfile, PlanResult, Target, dynamic_components_v2_target, plan
from squid_ui.planning.identity import has_routed_page
from squid_ui.planning.navigation import PlannedNav, default_nav
from squid_ui.semantic import ActionControl, AnyLayoutNode, LayoutNode, RoutedActionControl, RoutedChoices
from squid_ui.target_types import DiscordAdapter, RenderTarget
from squid_ui_widgets.drivers import (
    _MISSING_INITIAL_STATE,
    ComponentDriver,
    FormValues,
    RouteDriver,
    StateMachine,
    TransitionHandler,
    TransitionRoute,
    _MissingInitialState,
)


@dataclass
class MachineHarness[StateT, RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
    """One machine's component shell, rendered and driven through its own semantic tree.

    `nodes` re-renders on every read, so a test reads the tree after a press the way a frontend
    would rather than holding a stale one.
    """

    driver: ComponentDriver[StateT, RenderTargetT, ContextT]
    responder: engine.RecordingResponder

    @property
    def state(self) -> StateT:
        """Return the mounted machine's current state."""
        return self.driver.machine_state

    @property
    def nodes(self) -> tuple[AnyLayoutNode, ...]:
        """Render the mounted machine's current semantic tree."""
        return engine.render_tree(self.driver)

    def texts(self) -> list[str]:
        """Return authored text from the current tree."""
        return engine.texts(self.nodes)

    def labels(self) -> list[str]:
        """Return control labels from the current tree."""
        return engine.labels(self.nodes)

    def keys(self) -> list[str]:
        """Return semantic keys from the current tree."""
        return engine.keys(self.nodes)

    def control(self, key: str) -> ActionControl:
        """The single control keyed `key` in the current render."""
        return engine.control(self.nodes, key)

    async def press(self, key: str, *, actor: str = "1") -> None:
        """Invoke the keyed semantic control handler with a recording responder."""
        await engine.press(self.driver, key, actor=actor, responder=self.responder)

    async def choose(self, key: str, *values: str, actor: str = "1") -> None:
        """Settle the picker keyed `key` on `values`."""
        await engine.choose(self.driver, key, *values, actor=actor, responder=self.responder)

    async def submit(self, key: str, values: FormValues, *, actor: str = "1") -> None:
        """Submit `values` to the form the trigger keyed `key` opens."""
        await engine.submit(self.driver, key, values, actor=actor, responder=self.responder)

    @property
    def notices(self) -> tuple[str, ...]:
        """Return notices recorded by the action responder."""
        return tuple(text for text, _visibility in self.responder.notices)

    @property
    def finished(self) -> bool:
        """Return whether an action finished the presentation."""
        return self.responder.finished


@overload
def mounted[StateT, RenderTargetT: RenderTarget, ContextT = object](
    machine: StateMachine[StateT, RenderTargetT, ContextT],
    *,
    on_change: TransitionHandler[StateT] | None = None,
    handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
    finish_actions: Collection[str] = (),
) -> MachineHarness[StateT, RenderTargetT, ContextT]: ...


@overload
def mounted[StateT, RenderTargetT: RenderTarget, ContextT = object](
    machine: StateMachine[StateT, RenderTargetT, ContextT],
    *,
    initial: StateT,
    on_change: TransitionHandler[StateT] | None = None,
    handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
    finish_actions: Collection[str] = (),
) -> MachineHarness[StateT, RenderTargetT, ContextT]: ...


def mounted[StateT, RenderTargetT: RenderTarget, ContextT = object](
    machine: StateMachine[StateT, RenderTargetT, ContextT],
    *,
    initial: StateT | _MissingInitialState = _MISSING_INITIAL_STATE,
    on_change: TransitionHandler[StateT] | None = None,
    handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
    finish_actions: Collection[str] = (),
) -> MachineHarness[StateT, RenderTargetT, ContextT]:
    """A `machine` in a bare component shell, ready to be driven by key.

    Builds the driver directly, so a machine's *own* wiring is not applied -- `Menu` finishes
    the shell on close, `Decision` can finish on a chosen option, and both do that inside
    `build_component`. A test about any of it wants `driving(machine.build_component(...))`.
    """
    if isinstance(initial, _MissingInitialState):
        driver: ComponentDriver[StateT, RenderTargetT, ContextT] = ComponentDriver(
            machine,
            on_change=on_change,
            handlers=handlers,
            finish_actions=finish_actions,
        )
    else:
        driver = ComponentDriver(
            machine,
            initial=initial,
            on_change=on_change,
            handlers=handlers,
            finish_actions=finish_actions,
        )
    return MachineHarness(driver, engine.RecordingResponder())


def driving[StateT, RenderTargetT: RenderTarget, ContextT = object](
    driver: ComponentDriver[StateT, RenderTargetT, ContextT],
) -> MachineHarness[StateT, RenderTargetT, ContextT]:
    """Wrap a widget's configured component, preserving its callbacks and finish policy.

    Use driving(machine.build_component(...)) when testing that widget-specific wiring;
    mounted builds a bare driver instead.
    """
    return MachineHarness(driver, engine.RecordingResponder())


@dataclass(frozen=True, slots=True)
class RoutedRender[StateT, RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
    """One stateless render and every route it asked the host to encode."""

    nodes: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    routes: tuple[TransitionRoute[StateT], ...]
    planned: PlanResult[Any] | None = None

    def texts(self) -> list[str]:
        """Return authored text from this routed tree."""
        return engine.texts(self.nodes if self.planned is None else self.planned.scene)

    def labels(self) -> list[str]:
        """Return control labels from this routed tree."""
        return engine.labels(self.nodes if self.planned is None else self.planned.scene)

    def route_ids(self) -> list[str]:
        """Every encoded id in the render, in order, across controls and pickers."""
        if self.planned is not None:
            return [
                node.route_id
                for node in engine.walk(self.planned.scene)
                if isinstance(node, scene.RoutedButton | scene.RoutedSelect)
            ] + [node.route_id for node in engine.walk(self.planned.scene) if isinstance(node, scene.HtmlRouteRef)]
        return [
            node.route_id for node in engine.walk(self.nodes) if isinstance(node, RoutedActionControl | RoutedChoices)
        ]

    def route_for(self, action: str) -> TransitionRoute[StateT]:
        """The single route this render asked for under `action`."""
        found = [route for route in self.routes if route.action == action]
        assert found, f"no route for {action!r}; this render asked for {[route.action for route in self.routes]}"
        assert len(found) == 1, f"{len(found)} routes for {action!r}"
        return found[0]


@dataclass
class _RouteRecorder[StateT]:
    """Record requested routes and assign stable test IDs."""

    routes: list[TransitionRoute[StateT]] = field(default_factory=list)

    def __call__(self, request: TransitionRoute[StateT]) -> str:
        self.routes.append(request)
        return self.id_for(len(self.routes) - 1)

    def id_for(self, index: int) -> str:
        return f"route:{index}"


@overload
def routed[StateT, RenderTargetT: RenderTarget, ContextT = object](
    machine: StateMachine[StateT, RenderTargetT, ContextT],
    *,
    chrome: Chrome = DEFAULT_CHROME,
    target: Target[Any, Any, Any, Any] | None = None,
    nav: PlannedNav = default_nav,
) -> RoutedRender[StateT, RenderTargetT, ContextT]: ...


@overload
def routed[StateT, RenderTargetT: RenderTarget, ContextT = object](
    machine: StateMachine[StateT, RenderTargetT, ContextT],
    state: StateT,
    *,
    chrome: Chrome = DEFAULT_CHROME,
    target: Target[Any, Any, Any, Any] | None = None,
    nav: PlannedNav = default_nav,
) -> RoutedRender[StateT, RenderTargetT, ContextT]: ...


def routed[StateT, RenderTargetT: RenderTarget, ContextT = object](
    machine: StateMachine[StateT, RenderTargetT, ContextT],
    state: StateT | _MissingInitialState = _MISSING_INITIAL_STATE,
    *,
    chrome: Chrome = DEFAULT_CHROME,
    target: Target[Any, Any, Any, Any] | None = None,
    nav: PlannedNav = default_nav,
) -> RoutedRender[StateT, RenderTargetT, ContextT]:
    """Record routes, planning when a target is supplied or the tree has routed pagination.

    Routed pagination defaults to a protocol-only Discord V2 target. `planned` exposes
    its scene; `routes` includes only IDs reachable in that final scene, not discarded
    candidates. Other calls retain the lightweight semantic-render behavior.
    """
    recorder: _RouteRecorder[StateT] = _RouteRecorder()
    driver: RouteDriver[StateT, RenderTargetT, ContextT] = RouteDriver(recorder, chrome)
    result = driver.render(machine, machine.initial_state if isinstance(state, _MissingInitialState) else state)
    if target is None and has_routed_page(result):
        target = dynamic_components_v2_target(AdapterProfile(DiscordAdapter, "widget-tests", "1"))
    # This helper inspects scenes; it never supplies the callbacks' application context.
    planned = None if target is None else plan(cast(DocumentLike[Any], result), target=target, chrome=chrome, nav=nav)
    rendered = RoutedRender(as_document(result).children, tuple(recorder.routes), planned)
    if planned is None:
        return rendered
    ids = set(rendered.route_ids())
    routes = tuple(route for index, route in enumerate(recorder.routes) if recorder.id_for(index) in ids)
    return RoutedRender(rendered.nodes, routes, planned)


__all__ = [
    "MachineHarness",
    "RoutedRender",
    "driving",
    "mounted",
    "routed",
]

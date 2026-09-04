"""Drive a machine's two shells with no frontend attached.

A machine owns its state and the tree it describes; a frontend only carries presses back to
it. `mounted` supplies the component shell and drives it by the same semantic key a Discord
custom id would carry, and `routed` renders the stateless shell and reports every route it
asked for. Between them, everything a widget itself owns is reachable without a transport --
which is what lets these tests live beside the machines rather than beside the adapter.

What is deliberately out of reach: whether the rendered tree fits a target's limits, and
whether a key survives planning to become a dispatchable id. Both are facts about a frontend,
and `squid_ui_discord`'s suite is where they are asserted.

This module is public and versioned like the rest of the package. It is imported by tests
rather than by a running application, so it is reachable as `squid_ui_widgets.testing.X` and
promotes no names to `squid_ui_widgets` itself.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import overload

from squid_ui import testing as engine
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike, as_document
from squid_ui.semantic import ActionControl, AnyLayoutNode, LayoutNode, RoutedActionControl, RoutedChoices
from squid_ui.target_types import RenderTarget
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
class MachineHarness[StateT, RenderTargetT: RenderTarget = RenderTarget]:
    """One machine's component shell, rendered and driven through its own semantic tree.

    `nodes` re-renders on every read, so a test reads the tree after a press the way a frontend
    would rather than holding a stale one.
    """

    driver: ComponentDriver[StateT, RenderTargetT]
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
        """Press the control keyed `key`, the way a frontend's dispatch would."""
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
def mounted[StateT, RenderTargetT: RenderTarget](
    machine: StateMachine[StateT, RenderTargetT],
    *,
    on_change: TransitionHandler[StateT] | None = None,
    handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
    finish_actions: Collection[str] = (),
) -> MachineHarness[StateT, RenderTargetT]: ...


@overload
def mounted[StateT, RenderTargetT: RenderTarget](
    machine: StateMachine[StateT, RenderTargetT],
    *,
    initial: StateT,
    on_change: TransitionHandler[StateT] | None = None,
    handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
    finish_actions: Collection[str] = (),
) -> MachineHarness[StateT, RenderTargetT]: ...


def mounted[StateT, RenderTargetT: RenderTarget](
    machine: StateMachine[StateT, RenderTargetT],
    *,
    initial: StateT | _MissingInitialState = _MISSING_INITIAL_STATE,
    on_change: TransitionHandler[StateT] | None = None,
    handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
    finish_actions: Collection[str] = (),
) -> MachineHarness[StateT, RenderTargetT]:
    """A `machine` in a bare component shell, ready to be driven by key.

    Builds the driver directly, so a machine's *own* wiring is not applied -- `Menu` finishes
    the shell on close, `Decision` can finish on a chosen option, and both do that inside
    `build_component`. A test about any of it wants `driving(machine.build_component(...))`.
    """
    if isinstance(initial, _MissingInitialState):
        driver: ComponentDriver[StateT, RenderTargetT] = ComponentDriver(
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


def driving[StateT, RenderTargetT: RenderTarget](
    driver: ComponentDriver[StateT, RenderTargetT],
) -> MachineHarness[StateT, RenderTargetT]:
    """A harness around a component a machine built for itself.

    `mounted` constructs the driver directly, which cannot reach a machine's own builder
    arguments -- `Decision.build_component(on_decide=..., finish_on=...)` and its siblings. A
    test about those wires the component the way an application does, then drives it here.
    """
    return MachineHarness(driver, engine.RecordingResponder())


@dataclass(frozen=True, slots=True)
class RoutedRender[StateT, RenderTargetT: RenderTarget = RenderTarget]:
    """One stateless render and every route it asked the host to encode."""

    nodes: tuple[LayoutNode[RenderTargetT], ...]
    routes: tuple[TransitionRoute[StateT], ...]

    def texts(self) -> list[str]:
        """Return authored text from this routed tree."""
        return engine.texts(self.nodes)

    def labels(self) -> list[str]:
        """Return control labels from this routed tree."""
        return engine.labels(self.nodes)

    def route_ids(self) -> list[str]:
        """Every encoded id in the render, in order, across controls and pickers."""
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
    """The encoder every routed test used to hand-roll as a list plus a closure."""

    routes: list[TransitionRoute[StateT]] = field(default_factory=list)

    def __call__(self, request: TransitionRoute[StateT]) -> str:
        self.routes.append(request)
        return f"route:{len(self.routes) - 1}:{request.action}"


@overload
def routed[StateT, RenderTargetT: RenderTarget](
    machine: StateMachine[StateT, RenderTargetT],
    *,
    chrome: Chrome = DEFAULT_CHROME,
) -> RoutedRender[StateT, RenderTargetT]: ...


@overload
def routed[StateT, RenderTargetT: RenderTarget](
    machine: StateMachine[StateT, RenderTargetT],
    state: StateT,
    *,
    chrome: Chrome = DEFAULT_CHROME,
) -> RoutedRender[StateT, RenderTargetT]: ...


def routed[StateT, RenderTargetT: RenderTarget](
    machine: StateMachine[StateT, RenderTargetT],
    state: StateT | _MissingInitialState = _MISSING_INITIAL_STATE,
    *,
    chrome: Chrome = DEFAULT_CHROME,
) -> RoutedRender[StateT, RenderTargetT]:
    """Render `machine` in its stateless shell and report the routes it asked for.

    The encoder is supplied rather than taken, because what a test wants to see is *which*
    routes a render requested and in what order -- not what a particular host spells them.
    """
    recorder: _RouteRecorder[StateT] = _RouteRecorder()
    driver: RouteDriver[StateT, RenderTargetT] = RouteDriver(recorder, chrome)
    result = driver.render(machine, machine.initial_state if isinstance(state, _MissingInitialState) else state)
    return RoutedRender(_as_nodes(result), tuple(recorder.routes))


def _as_nodes[RenderTargetT: RenderTarget](
    result: DocumentLike[RenderTargetT],
) -> tuple[LayoutNode[RenderTargetT], ...]:
    """A `DocumentLike` is one node, a sequence of them, or a document; queries want a tuple."""
    return as_document(result).children


__all__ = [
    "MachineHarness",
    "RoutedRender",
    "driving",
    "mounted",
    "routed",
]

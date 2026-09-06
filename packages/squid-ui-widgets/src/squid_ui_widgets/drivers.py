"""The two shells shared by every interactive machine."""

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, overload, runtime_checkable

from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike
from squid_ui.factories import action_control, choice, choices, controlled, form, routed_action_control, routed_choices
from squid_ui.forms import Form, FormLike, FormSpec
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.runtime.component import Component
from squid_ui.runtime.reactivity import state
from squid_ui.semantic import (
    ActionControl,
    Choice,
    ChoiceEvent,
    Choices,
    Emphasis,
    FormTrigger,
    LayoutNode,
    RoutedActionControl,
    RoutedChoices,
    Tone,
)
from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentItem


@dataclass(frozen=True, slots=True)
class TransitionRoute[StateT]:
    """One routed interaction and the state its custom id must carry.

    ``phase="next"`` means a button's deterministic transition has already been
    applied. ``phase="input"`` means the select or form values arrive with the
    interaction, so the id necessarily carries the state they apply to.
    """

    action: str
    state: StateT
    phase: Literal["next", "input"]


@dataclass(frozen=True, slots=True)
class TransitionEvent[StateT]:
    """A shell interaction after the machine transition has been applied.

    `state` may equal `previous` when the machine ignored the action. `source` is the
    frontend event, still open for `acknowledge`/`finish`.
    """

    source: ActionEvent
    action: str
    previous: StateT
    state: StateT
    values: tuple[str, ...] = ()
    """Selected keys when the interaction came from a picker."""
    submitted: Mapping[str, object] | None = None
    """Parsed field values when the interaction came from a form."""


type TransitionHandler[StateT] = Callable[[TransitionEvent[StateT]], Awaitable[None]]
type RouteEncoder[StateT] = Callable[[TransitionRoute[StateT]], str]
"""Host-owned: turns a route into the custom id it can decode back into `TransitionRoute.state`."""


class _MissingInitialState:
    pass


_MISSING_INITIAL_STATE = _MissingInitialState()


class StateMachine[StateT, RenderTargetT: RenderTarget = RenderTarget](Protocol):
    """A pure state machine that describes a tree through injected controls.

    `StateT` must be immutable and equality-comparable: the shells compare states to detect
    change, and `RouteDriver` encodes them into custom ids. Neither shell calls anything else.
    """

    @property
    def initial_state(self) -> StateT:
        """The state a shell starts from when the caller supplies none."""
        ...

    def render(self, state: StateT, controls: MachineControls[StateT, RenderTargetT]) -> DocumentLike[RenderTargetT]:
        """Describe `state`; every interactive node comes from `controls`, never from the factories directly."""
        ...

    def transition(
        self,
        state: StateT,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> StateT:
        """The state after `action`; return `state` itself for an unknown or currently invalid action.

        `values` carries a picker's selection, `submitted` a form's parsed fields. `RouteDriver`
        calls this eagerly for every button while rendering, so it must be side-effect free.
        """
        ...


@runtime_checkable
class FormPresentingMachine[StateT, RenderTargetT: RenderTarget = RenderTarget](
    StateMachine[StateT, RenderTargetT], Protocol
):
    """A machine that can answer one of its own actions with a form.

    Not every machine has forms, so this is a separate shape rather than an optional method
    on `StateMachine`. `Editor` resolves nested sections through it.
    """

    def form_for(self, state: StateT, action: str) -> FormSpec | None:
        """The form a route host presents for `action` in `state`; `None` when that action opens no form."""
        ...


class MachineControls[StateT, RenderTargetT: RenderTarget = RenderTarget](Protocol):
    """Control and content construction injected into a pure machine render.

    `action_name` on every member is the machine action the interaction dispatches; `key` is
    the semantic key the frontend addresses the node by. Which concrete node comes back
    depends on the shell: closure-backed in `ComponentDriver`, route-backed in `RouteDriver`.
    """

    @property
    def chrome(self) -> Chrome:
        """Wording for the shell's own controls, from the mount's context or `DEFAULT_CHROME`."""
        ...

    def content(
        self, content: Sequence[ContentItem[RenderTargetT]], *, prefix: str
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        """Embed a normalized content slot, keying each child `Component` as `<prefix>-<index>`.

        Raises `TypeError` in the route shell when `content` holds a `Component`.
        """
        ...

    def action_control(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> ActionControl | RoutedActionControl:
        """A button dispatching `action_name` with no payload."""
        ...

    def choices(
        self,
        entries: Sequence[Choice],
        action_name: str,
        *,
        key: str,
        selected: tuple[str, ...],
        minimum: int,
        maximum: int,
        placeholder: TextLike | None = None,
        available: bool = True,
    ) -> Choices | RoutedChoices:
        """A picker dispatching `action_name` with the chosen keys as `values`.

        `selected` marks the current selection in the component shell only; the route shell
        cannot preselect. `placeholder` is honoured by the route shell only.
        """
        ...

    def form(
        self,
        spec: FormLike,
        action_name: str,
        *,
        key: str,
        label: TextLike,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
    ) -> FormTrigger | RoutedActionControl:
        """A trigger opening `spec` and dispatching `action_name` with the parsed fields as `submitted`.

        The route shell ignores `spec`: the host resolves it later through `FormPresentingMachine.form_for`.
        """
        ...


class ComponentDriver[StateT, RenderTargetT: RenderTarget = RenderTarget](Component[RenderTargetT]):
    """Host a machine as a `Component`, keeping its state in memory and dispatching through closures.

    On each interaction: transition, then `on_change`, then the `handlers` entry for that
    action, then `event.finish()` if the action is in `finish_actions`. `machine_state` is
    never persisted; `RouteDriver` is the shell for state that must survive a restart.
    """

    # RouteDriver is the restart boundary; a generic state dataclass has no honest JSON
    # decoder for durable component restoration.
    machine_state: StateT = state(persist=False)

    @overload
    def __init__(
        self,
        machine: StateMachine[StateT, RenderTargetT],
        *,
        on_change: TransitionHandler[StateT] | None = None,
        handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
        finish_actions: Collection[str] = (),
    ) -> None: ...

    @overload
    def __init__(
        self,
        machine: StateMachine[StateT, RenderTargetT],
        *,
        initial: StateT,
        on_change: TransitionHandler[StateT] | None = None,
        handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
        finish_actions: Collection[str] = (),
    ) -> None: ...

    def __init__(
        self,
        machine: StateMachine[StateT, RenderTargetT],
        *,
        initial: StateT | _MissingInitialState = _MISSING_INITIAL_STATE,
        on_change: TransitionHandler[StateT] | None = None,
        handlers: Mapping[str, TransitionHandler[StateT]] | None = None,
        finish_actions: Collection[str] = (),
    ) -> None:
        self.machine = machine
        self.machine_state = machine.initial_state if isinstance(initial, _MissingInitialState) else initial
        self.on_change = on_change
        self.handlers = dict(handlers or {})
        self.finish_actions = frozenset(finish_actions)

    def render(self) -> DocumentLike[RenderTargetT]:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        return self.machine.render(self.machine_state, _ComponentControls(self, chrome))

    async def _dispatch(
        self,
        event: ActionEvent,
        action_name: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> None:
        previous = self.machine_state
        current = self.machine.transition(previous, action_name, values=values, submitted=submitted)
        self.machine_state = current
        transition_event = TransitionEvent(event, action_name, previous, current, values, submitted)
        if self.on_change is not None:
            await self.on_change(transition_event)
        if handler := self.handlers.get(action_name):
            await handler(transition_event)
        if action_name in self.finish_actions:
            await event.finish()


class _ComponentControls[StateT, RenderTargetT: RenderTarget]:
    def __init__(self, owner: ComponentDriver[StateT, RenderTargetT], chrome: Chrome) -> None:
        self.owner = owner
        self.chrome = chrome

    def content(
        self, content: Sequence[ContentItem[RenderTargetT]], *, prefix: str
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        return tuple(
            self.owner.boundary(item, key=f"{prefix}-{index}") if isinstance(item, Component) else item
            for index, item in enumerate(content)
        )

    def action_control(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> ActionControl:
        async def trigger(event: ActionEvent) -> None:
            await self.owner._dispatch(event, action_name)

        return action_control(label, trigger, key=key, tone=tone, emphasis=emphasis, available=available)

    def choices(
        self,
        entries: Sequence[Choice],
        action_name: str,
        *,
        key: str,
        selected: tuple[str, ...],
        minimum: int,
        maximum: int,
        placeholder: TextLike | None = None,
        available: bool = True,
    ) -> Choices:
        del placeholder

        async def choose(event: ChoiceEvent) -> None:
            await self.owner._dispatch(event, action_name, values=event.selected)

        visible = tuple(
            entry if available else choice(entry.label, key=entry.key, available=False) for entry in entries
        )
        return choices(
            *visible,
            key=key,
            selection=controlled(selected, choose),
            minimum=minimum,
            maximum=maximum,
        )

    def form(
        self,
        spec: FormLike,
        action_name: str,
        *,
        key: str,
        label: TextLike,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
    ) -> FormTrigger:
        resolved = spec.spec() if isinstance(spec, Form) else spec

        async def submitted(event: SubmitEvent) -> None:
            await self.owner._dispatch(event, action_name, submitted=event.values)

        return form(label, resolved, key=key, on_submit=submitted, tone=tone, emphasis=emphasis)


@dataclass(frozen=True, slots=True)
class RouteDriver[StateT, RenderTargetT: RenderTarget = RenderTarget]:
    """Inject route-backed controls into a stateless machine render.

    A host route decodes ``TransitionRoute.state``, calls :meth:`transition` when the
    interaction carries input, and replaces the whole message with a fresh render. Content
    slots cannot hold `Component`s in this shell: `render` raises `TypeError` for one.
    """

    route: RouteEncoder[StateT]
    chrome: Chrome = DEFAULT_CHROME

    def render(self, machine: StateMachine[StateT, RenderTargetT], state: StateT) -> DocumentLike[RenderTargetT]:
        """Render `state`, computing and encoding every button's next state now."""
        return machine.render(state, _RoutedControls(machine, state, self.route, self.chrome))

    def transition(
        self,
        machine: StateMachine[StateT, RenderTargetT],
        state: StateT,
        action_name: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> StateT:
        """Apply input received by a routed select or form handler."""
        return machine.transition(state, action_name, values=values, submitted=submitted)


class _RoutedControls[StateT, RenderTargetT: RenderTarget]:
    def __init__(
        self,
        machine: StateMachine[StateT, RenderTargetT],
        current: StateT,
        route: RouteEncoder[StateT],
        chrome: Chrome,
    ) -> None:
        self.machine = machine
        self.current = current
        self.route = route
        self.chrome = chrome

    def content(
        self, content: Sequence[ContentItem[RenderTargetT]], *, prefix: str
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        del prefix
        nodes: list[LayoutNode[RenderTargetT]] = []
        for item in content:
            if isinstance(item, Component):
                message = (
                    f"a routed machine cannot embed {type(item).__name__}; "
                    "render frontend-neutral content from route state instead"
                )
                raise TypeError(message)
            nodes.append(item)
        return tuple(nodes)

    def action_control(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> RoutedActionControl:
        next_state = self.machine.transition(self.current, action_name)
        route_id = self.route(TransitionRoute(action_name, next_state, "next"))
        return routed_action_control(label, route_id, key=key, tone=tone, emphasis=emphasis, available=available)

    def choices(
        self,
        entries: Sequence[Choice],
        action_name: str,
        *,
        key: str,
        selected: tuple[str, ...],
        minimum: int,
        maximum: int,
        placeholder: TextLike | None = None,
        available: bool = True,
    ) -> RoutedChoices:
        del selected
        route_id = self.route(TransitionRoute(action_name, self.current, "input"))
        return routed_choices(
            *entries,
            route_id=route_id,
            key=key,
            placeholder=placeholder,
            minimum=minimum,
            maximum=maximum,
            available=available,
        )

    def form(
        self,
        spec: FormLike,
        action_name: str,
        *,
        key: str,
        label: TextLike,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
    ) -> RoutedActionControl:
        del spec
        route_id = self.route(TransitionRoute(action_name, self.current, "input"))
        return routed_action_control(label, route_id, key=key, tone=tone, emphasis=emphasis)

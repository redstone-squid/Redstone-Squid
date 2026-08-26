"""The two shells shared by every interactive pattern."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME, Chrome
from squid_ui.factories import action, choice, choices, controlled, form, routed_action, routed_choices
from squid_ui.forms import Form, FormLike
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.runtime.component import Component, RenderResult
from squid_ui.runtime.reactivity import state
from squid_ui.semantic import (
    Action,
    Choice,
    ChoiceEvent,
    Choices,
    Emphasis,
    FormTrigger,
    LayoutNode,
    RoutedAction,
    RoutedChoices,
    Tone,
)
from squid_ui.text import TextLike
from squid_patterns._content import ContentItem


@dataclass(frozen=True, slots=True)
class PatternRoute[StateT]:
    """One routed interaction and the state its custom id must carry.

    ``phase="next"`` means a button's deterministic transition has already been
    applied. ``phase="input"`` means the select or form values arrive with the
    interaction, so the id necessarily carries the state they apply to.
    """

    action: str
    state: StateT
    phase: Literal["next", "input"]


@dataclass(frozen=True, slots=True)
class PatternEvent[StateT]:
    """A shell interaction after the pattern transition has been applied."""

    source: ActionEvent
    action: str
    previous: StateT
    state: StateT
    values: tuple[str, ...] = ()
    submitted: Mapping[str, object] | None = None


type PatternHandler[StateT] = Callable[[PatternEvent[StateT]], Awaitable[None]]
type RouteBuilder[StateT] = Callable[[PatternRoute[StateT]], str]


class Pattern[StateT](Protocol):
    """A pure state machine that describes a tree through injected controls."""

    @property
    def initial_state(self) -> StateT: ...

    def render(self, state: StateT, controls: PatternControls[StateT]) -> RenderResult: ...

    def transition(
        self,
        state: StateT,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> StateT: ...


class PatternControls[StateT](Protocol):
    """Control and content construction injected into a pure pattern render."""

    @property
    def chrome(self) -> Chrome: ...

    def content(self, content: Sequence[ContentItem], *, prefix: str) -> tuple[LayoutNode, ...]: ...

    def action(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> Action | RoutedAction: ...

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
    ) -> Choices | RoutedChoices: ...

    def form(
        self,
        spec: FormLike,
        action_name: str,
        *,
        key: str,
        label: TextLike,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
    ) -> FormTrigger | RoutedAction: ...


class ComponentShell[StateT](Component):
    """Store pattern state in ``sl.state`` and inject closure-backed controls."""

    # RouterShell is the restart boundary; a generic state dataclass has no honest JSON
    # decoder for durable component restoration.
    pattern_state: Any = state(persist=False)

    def __init__(
        self,
        pattern: Pattern[StateT],
        *,
        initial: StateT | None = None,
        on_change: PatternHandler[StateT] | None = None,
        handlers: Mapping[str, PatternHandler[StateT]] | None = None,
        finish_actions: Sequence[str] = (),
    ) -> None:
        self.pattern = pattern
        self.pattern_state = pattern.initial_state if initial is None else initial
        self.on_change = on_change
        self.handlers = dict(handlers or {})
        self.finish_actions = frozenset(finish_actions)

    def render(self) -> RenderResult:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        return self.pattern.render(self.pattern_state, _ComponentControls(self, chrome))

    async def _dispatch(
        self,
        event: ActionEvent,
        action_name: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> None:
        previous = self.pattern_state
        current = self.pattern.transition(previous, action_name, values=values, submitted=submitted)
        self.pattern_state = current
        pattern_event = PatternEvent(event, action_name, previous, current, values, submitted)
        if self.on_change is not None:
            await self.on_change(pattern_event)
        if handler := self.handlers.get(action_name):
            await handler(pattern_event)
        if action_name in self.finish_actions:
            await event.finish()


class _ComponentControls[StateT]:
    def __init__(self, owner: ComponentShell[StateT], chrome: Chrome) -> None:
        self.owner = owner
        self.chrome = chrome

    def content(self, content: Sequence[ContentItem], *, prefix: str) -> tuple[LayoutNode, ...]:
        return tuple(
            self.owner.boundary(item, key=f"{prefix}-{index}") if isinstance(item, Component) else item
            for index, item in enumerate(content)
        )

    def action(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> Action:
        async def trigger(event: ActionEvent) -> None:
            await self.owner._dispatch(event, action_name)

        return action(label, trigger, key=key, tone=tone, emphasis=emphasis, available=available)

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
class RouterShell[StateT]:
    """Inject route-backed controls into a stateless pattern render.

    A host route decodes ``PatternRoute.state``, calls :meth:`transition` when the
    interaction carries input, and replaces the whole message with a fresh render.
    """

    route: RouteBuilder[StateT]
    chrome: Chrome = DEFAULT_CHROME

    def render(self, pattern: Pattern[StateT], state: StateT) -> RenderResult:
        return pattern.render(state, _RoutedControls(pattern, state, self.route, self.chrome))

    def transition(
        self,
        pattern: Pattern[StateT],
        state: StateT,
        action_name: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> StateT:
        """Apply input received by a routed select or form handler."""
        return pattern.transition(state, action_name, values=values, submitted=submitted)


class _RoutedControls[StateT]:
    def __init__(
        self,
        pattern: Pattern[StateT],
        current: StateT,
        route: RouteBuilder[StateT],
        chrome: Chrome,
    ) -> None:
        self.pattern = pattern
        self.current = current
        self.route = route
        self.chrome = chrome

    def content(self, content: Sequence[ContentItem], *, prefix: str) -> tuple[LayoutNode, ...]:
        del prefix
        if component := next((item for item in content if isinstance(item, Component)), None):
            message = (
                f"a routed pattern cannot embed {type(component).__name__}; "
                "render frontend-neutral content from route state instead"
            )
            raise TypeError(message)
        return tuple(content)  # type: ignore[bad-return]

    def action(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> RoutedAction:
        next_state = self.pattern.transition(self.current, action_name)
        route_id = self.route(PatternRoute(action_name, next_state, "next"))
        return routed_action(label, route_id, key=key, tone=tone, emphasis=emphasis, available=available)

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
        route_id = self.route(PatternRoute(action_name, self.current, "input"))
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
    ) -> RoutedAction:
        del spec
        route_id = self.route(PatternRoute(action_name, self.current, "input"))
        return routed_action(label, route_id, key=key, tone=tone, emphasis=emphasis)

"""Build actions, forms, selection controls, and their ownership bindings."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from squid_i18n import Text
from squid_ui._presentation import ValueBinding
from squid_ui.authoring.semantic import (
    FIRST_OPTION,
    NO_ENTITIES,
    OFF,
    UNRATED,
    UNSELECTED,
    ActionControl,
    ActionControls,
    Choice,
    ChoiceEvent,
    ChoiceOwnership,
    Choices,
    ControlDisplay,
    ControlGroup,
    Controlled,
    Download,
    Emphasis,
    Entities,
    EntityChoice,
    EntityOwnership,
    Flexibility,
    FormTrigger,
    Link,
    Navigation,
    NavigationDisplay,
    NavOption,
    NavOwnership,
    RoutedActionControl,
    RoutedChoices,
    ScaleEvent,
    ScaleOwnership,
    Toggle,
    ToggleOwnership,
    Tone,
    Uncontrolled,
)
from squid_ui.content.assets import FileInput, file
from squid_ui.content.entity import ConversationType, EntityRef, EntityType
from squid_ui.interactions import ActionMode, BusySpec, PressHandler
from squid_ui.interactions.forms import FormLike, SubmitHandler, bind_form
from squid_ui.interactions.guards import Guard
from squid_ui.palette import Role

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History

from squid_ui.factories._inputs import Conditional, TextValue, _collect


def controlled[ValueT, EventT](
    value: ValueT,
    on_change: Callable[[EventT], Awaitable[None]],
) -> Controlled[ValueT, EventT]:
    """Own this node's value yourself: it wins every render and ``on_change`` gets the writes."""
    return Controlled(value, on_change)


def uncontrolled[ValueT](initial: ValueT) -> Uncontrolled[ValueT]:
    """Store this node's value in PresentationState, using initial when its key is absent."""
    return Uncontrolled(initial)


def form[ContextT](
    label: TextValue,
    spec: FormLike[ContextT],
    *,
    key: str,
    on_submit: SubmitHandler[ContextT] | None = None,
    mode: ActionMode | None = None,
    tone: Tone | Role = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    guard: Guard | None = None,
    record: History | None = None,
) -> FormTrigger[ContextT]:
    """A content control that presents a portable form.

    ``guard`` gates the press that opens the form; the submission that follows completes an
    already-admitted press and is not checked again.
    """
    label = Text(label)
    resolved, handler, default_mode = bind_form(spec, on_submit)
    selected_mode = mode or default_mode
    if record is not None and selected_mode is ActionMode.PARALLEL_READ:
        message = "a parallel-read form submission changes nothing, so it has nothing to record"
        raise ValueError(message)
    return FormTrigger(key, label, resolved, handler, selected_mode, tone, emphasis, guard, record)


def action_control[ContextT = object](
    label: TextValue,
    on_trigger: PressHandler[ContextT],
    *,
    key: str,
    tone: Tone | Role = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    available: bool = True,
    allow_grouping: bool | None = None,
    mode: ActionMode = ActionMode.EXCLUSIVE,
    guard: Guard | None = None,
    busy: BusySpec | None = None,
    record: History | None = None,
) -> ActionControl[ContextT]:
    """A control that runs ``on_trigger``; ``key`` namespaces its custom id.

    ``available`` controls the rendered enabled state; ``guard`` checks admission when
    a press arrives. A disabled appearance does not replace an authorization check.

    ``record`` opens the entry for this press, under ``label``, before the handler runs: an
    action whose whole commit is transactional state needs no `History.record` of its own.
    To undo external effects as well, call History.record with a CompensationSpec
    inside the handler instead. Combining that call with ``record=`` raises HistoryError
    because both would reserve the same history entry.
    """
    label = Text(label)
    if record is not None and mode is ActionMode.PARALLEL_READ:
        message = "a parallel-read action changes nothing, so it has nothing to record"
        raise ValueError(message)
    return ActionControl(key, label, on_trigger, tone, emphasis, available, allow_grouping, mode, guard, busy, record)


def toggle[ContextT = object](
    label: TextValue,
    *,
    key: str,
    on: ToggleOwnership[ContextT] = OFF,
    on_label: TextValue | None = None,
    off_label: TextValue | None = None,
    tone: Tone | Role = Tone.NEUTRAL,
    available: bool = True,
) -> Toggle[ContextT]:
    """A boolean control; ``on`` declares whether the author or session owns its state."""
    label = Text(label)
    on_label = None if on_label is None else Text(on_label)
    off_label = None if off_label is None else Text(off_label)
    return Toggle(key, label, on, on_label, off_label, tone, available)


def download(
    source: FileInput,
    *,
    key: str,
    label: TextValue | None = None,
    filename: str | None = None,
    media_type: str | None = None,
    description: TextValue | None = None,
    emphasis: Emphasis = Emphasis.NORMAL,
    spoiler: bool = False,
) -> Download:
    """Offer a path, bytes, binary stream, or URL as a downloadable file.

    Bytes and unnamed streams need filename=. The default label is localized.
    Input ownership and snapshot behavior are the same as file().
    """
    label = None if label is None else Text(label)
    description = None if description is None else Text(description)
    return Download(
        key,
        label,
        file(source, filename=filename, media_type=media_type),
        description,
        emphasis,
        spoiler,
    )


def link(label: TextValue, url: str, *, key: str, emphasis: Emphasis = Emphasis.NORMAL) -> Link:
    """A control that opens ``url``."""
    label = Text(label)
    return Link(key, label, url, emphasis)


def routed_action_control(
    label: TextValue,
    route_id: str,
    *,
    key: str,
    tone: Tone | Role = Tone.NEUTRAL,
    emphasis: Emphasis = Emphasis.NORMAL,
    available: bool = True,
) -> RoutedActionControl:
    """A control the router dispatches, surviving the process that drew it.

    Build ``route_id`` with a `squid_ui.routing.Route` rather than by hand, so an id
    over Discord's budget fails here and not at send time.
    """
    label = Text(label)
    return RoutedActionControl(key, label, route_id, tone, emphasis, available)


def control_group[ContextT = object](
    *entries: Conditional[ActionControl[ContextT] | Link | RoutedActionControl],
    key: str,
    label: TextValue | None = None,
) -> ControlGroup[ContextT]:
    """Controls that belong together and degrade together."""
    label = None if label is None else Text(label)
    return ControlGroup(key, _collect(entries, (ActionControl, Link, RoutedActionControl), "sl.control_group()"), label)


def action_controls[ContextT](
    *entries: Conditional[
        ActionControl[ContextT]
        | Link
        | RoutedActionControl
        | Toggle[ContextT]
        | FormTrigger[ContextT]
        | ControlGroup[ContextT]
    ],
    key: str,
    display: ControlDisplay = ControlDisplay.AUTO,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> ActionControls[ContextT]:
    """Lay out controls in the given order. ``key`` identifies their presentation.

    On Discord, individual buttons share rows of up to five. A nested ``control_group``
    starts a separate row. AUTO lets eligible actions fold into a menu when space is tight.
    """
    return ActionControls(
        _collect(
            entries,
            (ActionControl, Link, RoutedActionControl, Toggle, FormTrigger, ControlGroup),
            "sl.action_controls()",
        ),
        key,
        display,
        flexibility,
    )


def choice(label: TextValue, *, key: str, description: TextValue | None = None, available: bool = True) -> Choice:
    """One option of a `choices` control; ``key`` is the value it submits."""
    label = Text(label)
    description = None if description is None else Text(description)
    return Choice(key, label, description, available)


def choices[ContextT = object](
    *entries: Conditional[Choice],
    key: str,
    selection: ChoiceOwnership[ContextT] = UNSELECTED,
    minimum: int = 1,
    maximum: int = 1,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Choices[ContextT]:
    """A control that selects among options; ``key`` namespaces its custom id."""
    return Choices(
        key,
        _collect(entries, (Choice,), "sl.choices()"),
        selection,
        minimum,
        maximum,
        flexibility,
    )


def entity_choice(
    ref: EntityRef,
    label: TextValue,
    *,
    description: TextValue | None = None,
    available: bool = True,
) -> EntityChoice:
    """One enumerated fallback for an entity picker."""
    label = Text(label)
    description = None if description is None else Text(description)
    return EntityChoice(ref, label, description, available)


def entities[ContextT = object](
    *entries: Conditional[EntityChoice],
    key: str,
    entity_type: EntityType,
    selection: EntityOwnership[ContextT] = NO_ENTITIES,
    minimum: int = 1,
    maximum: int = 1,
    conversation_types: tuple[ConversationType, ...] = (),
    placeholder: TextValue | None = None,
    flexibility: Flexibility = Flexibility.NORMAL,
) -> Entities[ContextT]:
    """Select frontend-resolved entities, optionally with an enumerated fallback."""
    placeholder = None if placeholder is None else Text(placeholder)
    return Entities(
        key,
        entity_type,
        _collect(entries, (EntityChoice,), "sl.entities()"),
        selection,
        minimum,
        maximum,
        conversation_types,
        placeholder,
        flexibility,
    )


def rating[ContextT = object](
    *,
    key: str,
    maximum: int = 5,
    value: ScaleOwnership[ContextT] = UNRATED,
    labels: Mapping[int, TextValue] | None = None,
) -> Choices[ContextT]:
    """Build a single-select ordinal scale from 1 through maximum.

    Default labels use cumulative stars through five points and numbers above five.
    Controlled changes are converted from choice keys into integer ScaleEvent values;
    the target planner chooses the actual control representation.
    """
    if maximum < 2:
        message = "sl.rating() needs a maximum of at least 2"
        raise ValueError(message)
    points = range(1, maximum + 1)
    named = {} if labels is None else labels
    entries = tuple(
        choice(named[point] if point in named else _stars(point, maximum), key=str(point)) for point in points
    )
    if isinstance(value, Uncontrolled):
        selection: ChoiceOwnership[ContextT] = Uncontrolled(() if value.initial is None else (str(value.initial),))
    else:
        chosen = value.value
        on_change = _RatingCommit(value) if isinstance(value, ValueBinding) else value.on_change

        rate = _RatingSelection(on_change)

        selection = Controlled((), rate) if chosen is None else Controlled((str(chosen),), rate)
    return Choices(key, entries, selection, minimum=1, maximum=1)


def _stars(point: int, maximum: int) -> str:
    """The default label for one scale point.

    Cumulative stars while the control is still a button row; above that a select of ten
    star strings is unreadable, so the number speaks for itself.
    """
    return "\N{BLACK STAR}" * point if maximum <= 5 else str(point)


def routed_choices(
    *entries: Conditional[Choice],
    route_id: str,
    key: str,
    placeholder: TextValue | None = None,
    minimum: int = 1,
    maximum: int = 1,
    available: bool = True,
    selected: tuple[str, ...] = (),
) -> RoutedChoices:
    """A stateless string picker dispatched by ``route_id`` with selected choice keys."""
    placeholder = None if placeholder is None else Text(placeholder)
    return RoutedChoices(
        key,
        _collect(entries, (Choice,), "sl.routed_choices()"),
        route_id,
        placeholder,
        minimum,
        maximum,
        available,
        selected,
    )


def nav_option(label: TextValue, *, key: str, available: bool = True) -> NavOption:
    """One place `navigation` can go."""
    label = Text(label)
    return NavOption(key, label, available)


def navigation[ContextT = object](
    *entries: Conditional[NavOption],
    key: str,
    current: NavOwnership[ContextT] = FIRST_OPTION,
    display: NavigationDisplay = NavigationDisplay.AUTO,
    flexibility: Flexibility = Flexibility.STABLE,
) -> Navigation[ContextT]:
    """Movement between the views of one message."""
    return Navigation(
        key,
        _collect(entries, (NavOption,), "sl.navigation()"),
        current,
        display,
        flexibility,
    )


@dataclass(frozen=True, slots=True)
class _RatingSelection[ContextT]:
    callback: Callable[[ScaleEvent[ContextT]], Awaitable[None]]

    async def __call__(self, event: ChoiceEvent[ContextT]) -> None:
        if event.selected:
            await self.callback(
                ScaleEvent(
                    event.actor,
                    event.responder,
                    event.locale,
                    event.metadata,
                    int(event.selected[0]),
                    context=event.context,
                    _transaction=event._transaction,
                )
            )


@dataclass(frozen=True, slots=True)
class _RatingCommit[ContextT]:
    binding: ValueBinding[int | None]

    async def __call__(self, event: ScaleEvent[ContextT]) -> None:
        await self.binding.commit(event, event.value)

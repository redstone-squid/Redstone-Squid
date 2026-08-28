"""Nominate the semantic decisions reachable in one selected fallback state."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import assert_never

from squid_ui.errors import LayoutInvariantError
from squid_ui.factories import is_layout_node
from squid_ui.planning.limits import MessageLimits
from squid_ui.planning.search import StrategyAxis, StrategyCandidate
from squid_ui.planning.semantic_adaptation.model import FallbackAxis, SemanticDecisions
from squid_ui.planning.structure import (
    branch_paths,
    disclosure_state,
    fallback_rung,
    indexed_children,
    item_state,
)
from squid_ui.primitives.nodes import (
    Boundary,
    Break,
    Budget,
    Button,
    Card,
    Content,
    EntitySelect,
    Extension,
    Footer,
    Gallery,
    Lines,
    LinkButton,
    MediaCollection,
    Panel,
    PremiumButton,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Sep,
    Text,
    Thumbnail,
    Time,
    Variants,
    ZonedTime,
)
from squid_ui.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_ui.primitives.nodes import (
    ControlGroup as PrimitiveActionGroup,
)
from squid_ui.primitives.nodes import (
    File as PrimitiveFile,
)
from squid_ui.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_ui.primitives.nodes import (
    Section as PrimitiveSection,
)
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.semantic import (
    ActionControl,
    ActionControls,
    Article,
    Aside,
    BestEffort,
    Block,
    Budgeted,
    Choices,
    Cluster,
    Code,
    ControlDisplay,
    ControlGroup,
    Details,
    Download,
    Entities,
    FallbackContent,
    Fields,
    Figure,
    Flexibility,
    FormTrigger,
    Grid,
    Group,
    Heading,
    ItemDisplay,
    Items,
    KeepWithNext,
    LayoutNode,
    List,
    Media,
    Metric,
    Navigation,
    NavigationDisplay,
    Note,
    OptionalContent,
    Paged,
    Paragraph,
    ProgressBar,
    Quote,
    Roster,
    RoutedChoices,
    Section,
    Spilled,
    Stack,
    Status,
    Table,
    TableDisplay,
    Themed,
    Timestamp,
    Toggle,
    Truncated,
    Unbreakable,
    ZonedTimestamp,
)

ACTIONS_ADAPTER_ID = "discord.actions"
ACTIONS_ADAPTER_VERSION = 1
ITEMS_ADAPTER_ID = "discord.items"
ITEMS_ADAPTER_VERSION = 1
MEDIA_ADAPTER_ID = "discord.media"
MEDIA_ADAPTER_VERSION = 1
NAVIGATION_ADAPTER_ID = "discord.navigation"
NAVIGATION_ADAPTER_VERSION = 1
TABLE_ADAPTER_ID = "discord.table"
TABLE_ADAPTER_VERSION = 2
GRID_ADAPTER_ID = "discord.grid"
GRID_ADAPTER_VERSION = 1


def nominate_decisions(
    nodes: Sequence[LayoutNode],
    *,
    limits: MessageLimits,
    session: PresentationState,
    fallbacks: Mapping[str, int] | None = None,
) -> SemanticDecisions:
    """Collect the semantic decisions reachable through the selected fallback branches."""
    axes: list[StrategyAxis] = []
    occurrences: list[FallbackAxis] = []
    selected_rungs = {} if fallbacks is None else fallbacks

    def walk_children(children: Sequence[LayoutNode], path: str) -> None:
        for child, child_path in indexed_children(children, path):
            walk(child, child_path)

    def walk(node: LayoutNode, path: str) -> None:
        if not is_layout_node(node):
            message = f"{path}: {type(node).__name__} is neither a semantic node nor a Discord primitive"
            raise LayoutInvariantError(message)
        match node:
            case (
                Truncated(node=child)
                | Spilled(node=child)
                | BestEffort(node=child)
                | Budgeted(node=child)
                | Unbreakable(node=child)
                | KeepWithNext(node=child)
                | Paged(node=child)
            ):
                walk(child, path)
            case OptionalContent(node=child, importance=importance):
                occurrences.append(FallbackAxis(path, 2, branch_paths(path, 2), int(importance), optional=True))
                if fallback_rung(path, 2, selected_rungs) == 0:
                    walk(child, f"{path}.primary")
            case FallbackContent(primary=primary, alternates=alternates):
                branches = (primary, *alternates)
                rung = fallback_rung(path, len(branches), selected_rungs)
                occurrences.append(FallbackAxis(path, len(branches), branch_paths(path, len(branches))))
                walk(branches[rung], branch_paths(path, len(branches))[rung])
            case ActionControls():
                axes.append(action_axis(node, path, limits, session))
            case Table():
                axes.append(table_axis(node, path, session))
            case Grid():
                axes.append(grid_axis(node, path, limits, session))
            case Media():
                axes.append(media_axis(node, path, session))
            case Navigation():
                axes.append(navigation_axis(node, path, limits, session))
            case Items(items=items):
                axes.append(items_axis(node, path, limits, session))
                opened, fixed = item_state(node, session)
                if opened is None and not fixed and items:
                    opened = items[0].key
                if opened is not None:
                    item = next((item for item in items if item.key == opened), None)
                    if item is not None:
                        walk_children(item.children, f"{path}.{item.key}")
            case Group(children=children) | Stack(children=children) | Cluster(children=children):
                walk_children(children, path)
            case (
                Section(children=children)
                | Article(children=children)
                | Block(children=children)
                | Aside(children=children)
                | Themed(children=children)
                | Panel(children=children)
            ):
                walk_children(children, path)
            case Details(children=children):
                if disclosure_state(node, session):
                    walk_children(children, path)
            case (
                Heading()
                | Paragraph()
                | Note()
                | List()
                | Fields()
                | Quote()
                | Code()
                | Figure()
                | Toggle()
                | Download()
                | Status()
                | ProgressBar()
                | Roster()
                | Metric()
                | Timestamp()
                | ZonedTimestamp()
                | FormTrigger()
                | Choices()
                | Entities()
                | RoutedChoices()
            ):
                # Semantic leaves: no interior strategy axes and nothing semantic to descend
                # into. Named so a new leaf that does grow an axis cannot vanish silently.
                return
            case (
                Text()
                | PrimitiveHeading()
                | Footer()
                | PrimitiveCode()
                | Lines()
                | Time()
                | ZonedTime()
                | PrimitiveFile()
                | Sep()
                | Row()
                | PrimitiveActionGroup()
                | Button()
                | LinkButton()
                | SelectMenu()
                | EntitySelect()
                | RoutedSelect()
                | RoutedButton()
                | PremiumButton()
                | Thumbnail()
                | Gallery()
                | MediaCollection()
                | PrimitiveSection()
                | Budget()
                | Break()
                | RawItem()
                | Boundary()
                | Card()
                | Content()
                | Extension()
                | Variants()
            ):
                # Exact primitives carry no semantic decision axes; `Panel` alone is claimed
                # above because its children may hold semantic nodes, and `Variants` ladders
                # belong to frontier.py, not to semantic nomination.
                return
            case _ as unreachable:
                assert_never(unreachable)

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")
    paths = tuple(axis.path for axis in axes)
    if len(set(paths)) != len(paths):
        message = "semantic strategy paths must be unique"
        raise LayoutInvariantError(message)
    return SemanticDecisions(tuple(axes), tuple(occurrences))


def strategy_axis(
    *,
    path: str,
    key: str,
    adapter_id: str,
    adapter_version: int,
    flexibility: Flexibility,
    preferred: str,
    available: tuple[str, ...],
    order: tuple[str, ...],
    session: PresentationState,
    active_pagers: frozenset[str] = frozenset(),
) -> StrategyAxis:
    baseline = session.strategy(key, adapter_id, adapter_version)
    if baseline not in available:
        baseline = None
    reference = baseline or preferred
    positions = {strategy: index for index, strategy in enumerate(order)}
    candidates = tuple(
        StrategyCandidate(
            strategy,
            active_pagers=int(strategy in active_pagers),
            transition_distance=abs(positions[strategy] - positions.get(reference, positions[strategy])),
        )
        for strategy in available
    )
    return StrategyAxis(path, key, adapter_id, adapter_version, flexibility, preferred, candidates, baseline)


def individual_fits(controls: int, limits: MessageLimits) -> bool:
    rows = (controls + limits.components.row_buttons - 1) // limits.components.row_buttons
    return limits.fits_controls(controls, rows)


def items_axis(node: Items, path: str, limits: MessageLimits, session: PresentationState) -> StrategyAxis:
    opened, fixed = item_state(node, session)
    if fixed:
        available = ("opened",) if opened is not None else ("overview",)
    elif node.items:
        available = ("overview", "opened")
    else:
        available = ("overview",)
    preferred = (
        "opened"
        if opened is not None or (not fixed and node.display is ItemDisplay.OPENED and node.items)
        else "overview"
    )
    axis = strategy_axis(
        path=path,
        key=node.key,
        adapter_id=ITEMS_ADAPTER_ID,
        adapter_version=ITEMS_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=available,
        order=("overview", "opened"),
        session=session,
        active_pagers=frozenset({"overview"}) if len(node.items) > limits.components.select_options else frozenset(),
    )
    if opened is None and node.display is not ItemDisplay.OPENED:
        axis = replace(axis, baseline=None)
    return axis


def navigation_axis(node: Navigation, path: str, limits: MessageLimits, session: PresentationState) -> StrategyAxis:
    available = tuple(destination for destination in node.options if destination.available)
    strategies = ["individual"]
    if not individual_fits(len(available), limits):
        strategies.remove("individual")
    if available:
        strategies.append("grouped")
    preferred = {
        NavigationDisplay.INDIVIDUAL: "individual",
        NavigationDisplay.GROUPED: "grouped",
        NavigationDisplay.AUTO: "individual" if len(available) <= 5 else "grouped",
    }[node.display]
    if preferred not in strategies:
        preferred = strategies[-1]
    return strategy_axis(
        path=path,
        key=node.key,
        adapter_id=NAVIGATION_ADAPTER_ID,
        adapter_version=NAVIGATION_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=tuple(strategies),
        order=("individual", "grouped"),
        session=session,
        active_pagers=frozenset({"grouped"}) if len(available) > limits.components.select_options else frozenset(),
    )


def table_axis(node: Table, path: str, session: PresentationState) -> StrategyAxis:
    if node.display is TableDisplay.AUTO:
        preferred = "tabular" if len(node.columns.columns) <= 4 else "records"
        available = ("tabular", "records")
    else:
        preferred = node.display.value
        available = (preferred,)
    return strategy_axis(
        path=path,
        key=node.key,
        adapter_id=TABLE_ADAPTER_ID,
        adapter_version=TABLE_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=available,
        order=("matrix", "tabular", "records"),
        session=session,
    )


def grid_axis(node: Grid, path: str, limits: MessageLimits, session: PresentationState) -> StrategyAxis:
    rows = (len(node.cells) + node.columns - 1) // node.columns
    strategies: list[str] = []
    if node.columns <= limits.components.row_buttons and limits.fits_controls(len(node.cells), rows):
        strategies.append("buttons")
    available_cells = sum(cell.available for cell in node.cells)
    strategies.append("coordinate" if available_cells <= limits.components.select_options else "paged_select")
    available = tuple(strategies)
    return strategy_axis(
        path=path,
        key=node.key,
        adapter_id=GRID_ADAPTER_ID,
        adapter_version=GRID_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=available[0],
        available=available,
        order=("buttons", "coordinate", "paged_select"),
        session=session,
        active_pagers=frozenset({"paged_select"}),
    )


def media_axis(node: Media, path: str, session: PresentationState) -> StrategyAxis:
    preferred = "featured" if node.display.value == "featured" else "collection"
    return strategy_axis(
        path=path,
        key=node.key,
        adapter_id=MEDIA_ADAPTER_ID,
        adapter_version=MEDIA_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=("collection", "featured") if node.items else ("collection",),
        order=("collection", "featured"),
        session=session,
    )


def action_axis(node: ActionControls, path: str, limits: MessageLimits, session: PresentationState) -> StrategyAxis:
    actions = [action for item in node.items for action in contained_actions(item)]
    forced_pager = any(
        len(tuple(contained_actions(item))) > 75 for item in node.items if isinstance(item, ControlGroup)
    )
    if not any(isinstance(item, ControlGroup) for item in node.items):
        forced_pager = len(actions) > 75
    available = ["grouped", "paged"] if forced_pager else ["individual", "grouped"]
    if not individual_fits(len(actions), limits) and "individual" in available:
        available.remove("individual")
    preferred = {
        ControlDisplay.INDIVIDUAL: "individual",
        ControlDisplay.GROUPED: "grouped",
        ControlDisplay.AUTO: "individual" if len(actions) <= 5 else "grouped",
    }[node.display]
    if forced_pager:
        preferred = "paged"
    return strategy_axis(
        path=path,
        key=node.key,
        adapter_id=ACTIONS_ADAPTER_ID,
        adapter_version=ACTIONS_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=tuple(available),
        order=("individual", "grouped", "paged"),
        session=session,
        active_pagers=frozenset(available) if forced_pager else frozenset(),
    )


def contained_actions(item: ActionControl | object) -> Sequence[ActionControl]:
    if isinstance(item, ActionControl):
        return (item,)
    if isinstance(item, ControlGroup):
        return tuple(control for control in item.controls if isinstance(control, ActionControl))
    return ()

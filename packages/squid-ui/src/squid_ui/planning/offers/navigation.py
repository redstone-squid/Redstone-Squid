"""Build region navigation and stable identities for composed controls."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from urllib.parse import quote

from squid_i18n import tree
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Budget,
    SemanticRef,
)
from squid_ui.content.text import TextLike, resolve_text
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.interactions.ownership import Controlled
from squid_ui.native import NativeTraits, NativeValue
from squid_ui.overflow import (
    FIRST_PAGE,
    PageOwnership,
)
from squid_ui.planning import controls
from squid_ui.planning.composition import (
    CandidateComposition,
    FragmentCoverage,
    NavigationInput,
    OccurrenceId,
    Placement,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
)
from squid_ui.planning.control_style import button_style
from squid_ui.planning.interaction_handlers import ChoiceCommit, SelectChoices
from squid_ui.planning.interaction_semantics import DisclosureToggle, ItemBack, NavigationChoice
from squid_ui.planning.pagination.content import RootRegion
from squid_ui.planning.pagination.navigation import PagerNav, default_nav
from squid_ui.planning.realizations import (
    ActionsRealization,
    ControlRealization,
    Realization,
    SelectRealization,
    TextRealization,
)
from squid_ui.planning.semantic_index import UnsupportedSemanticError


@dataclass(frozen=True, slots=True)
class RegionNavigation[RenderTargetT, ContextT]:
    occurrence: SemanticOccurrence[RenderTargetT, ContextT]
    disabled_controls: bool
    footer: Callable[[int, int], TextLike] | None = None
    owner: PageOwnership = FIRST_PAGE
    cursor_key: str = ""
    default_controls: bool = False

    def __call__(
        self, page: int, pages: int, context: PlanningContext
    ) -> tuple[CandidateComposition[RenderTargetT, ContextT, Realization], ...]:
        if pages == 1:
            return ()
        occurrence = self.occurrence
        if context.mounted:
            label = (self.footer or context.chrome.page_footer)(page + 1, pages)
            placed = replace(
                occurrence,
                identity=OccurrenceId((*occurrence.identity.path, "@navigation", str(page))),
                placement=Placement.NAVIGATION,
                fragment=FragmentCoverage(occurrence.identity, 0, 0, frozenset()),
            )
            footer = CandidateComposition(
                RealizationOffer[RenderTargetT, ContextT, Realization](
                    replace(placed, identity=OccurrenceId((*placed.identity.path, "footer"))),
                    "footer",
                    TextRealization(resolve_text(label, context.localization).nodes),
                )
            )
            navigation = context.navigation
            if navigation is None and self.default_controls:
                navigation = PagerNav(default_nav, context.presentation, staged=True)
            controls = (
                () if navigation is None else navigation(self.cursor_key, page, pages, context.chrome, self.owner)
            )
            return (
                footer,
                *(
                    navigation_candidate(
                        node,
                        replace(placed, identity=OccurrenceId((*placed.identity.path, str(index)))),
                        context,
                        disabled_controls=self.disabled_controls,
                    )
                    for index, node in enumerate(controls)
                ),
            )
        route = "composition.page." + sha256("/".join(occurrence.identity.path).encode()).hexdigest()[:24]
        payloads: list[Realization] = []
        previous = "".join(
            tree.plain(node) for node in resolve_text(context.chrome.previous, context.localization).nodes
        )
        following = "".join(tree.plain(node) for node in resolve_text(context.chrome.next, context.localization).nodes)
        if page > 0 or self.disabled_controls:
            payloads.append(ControlRealization(previous, route=f"{route}.{max(0, page - 1)}", disabled=page == 0))
        label = (self.footer or context.chrome.page_footer)(page + 1, pages)
        payloads.append(TextRealization(resolve_text(label, context.localization).nodes))
        if page + 1 < pages or self.disabled_controls:
            payloads.append(
                ControlRealization(following, route=f"{route}.{min(pages - 1, page + 1)}", disabled=page + 1 == pages)
            )
        result: list[CandidateComposition[RenderTargetT, ContextT, Realization]] = []
        for index, payload in enumerate(payloads):
            context.work.consume()
            identity = OccurrenceId((*occurrence.identity.path, "@navigation", str(page), str(index)))
            placed = replace(
                occurrence,
                identity=identity,
                placement=Placement.NAVIGATION,
                fragment=FragmentCoverage(occurrence.identity, 0, 0, frozenset()),
            )
            navigation = None
            if isinstance(payload, ControlRealization) and payload.route is not None:
                navigation = NavigationInput(
                    occurrence.identity, payload.route, int(payload.route.rsplit(".", 1)[1]), not payload.disabled
                )
            result.append(
                CandidateComposition(
                    RealizationOffer[RenderTargetT, ContextT, Realization](
                        placed, "navigation", payload, navigation=navigation
                    )
                )
            )
        return tuple(result)


def navigation_candidate[R, C](
    node: object, occurrence: SemanticOccurrence[R, C], context: PlanningContext, *, disabled_controls: bool = True
) -> CandidateComposition[R, C, Realization]:
    """Expose target-independent navigation actions before resource certification."""
    if isinstance(node, NativeValue):
        return node.semantics.navigation_candidate(node, occurrence, context, disabled_controls=disabled_controls)
    context.work.consume()
    children = ()
    binding = None
    if isinstance(node, sem.ActionControls):
        payload = ActionsRealization(navigation=True)
        children = tuple(
            navigation_candidate(
                child, replace(occurrence, identity=OccurrenceId((*occurrence.identity.path, str(index)))), context
            )
            for index, child in enumerate(node.items)
            if disabled_controls
            or not isinstance(child, sem.ActionControl | sem.RoutedActionControl)
            or child.available
        )
    elif isinstance(node, sem.ActionControl | sem.RoutedActionControl):
        label = "".join(tree.plain(part) for part in resolve_text(node.label, context.localization).nodes)
        payload = ControlRealization(
            label,
            action=node.key if isinstance(node, sem.ActionControl) else None,
            route=node.route_id if isinstance(node, sem.RoutedActionControl) else None,
            style=button_style(node.tone, node.emphasis),
            disabled=not node.available,
            mode=node.mode if isinstance(node, sem.ActionControl) else ActionMode.EXCLUSIVE,
        )
        if isinstance(node, sem.ActionControl):
            binding = ActionBinding(
                node.key,
                node.on_trigger,
                node.mode,
                guard=node.guard,
                busy=node.busy,
                label=node.label,
                record=node.record,
            )
    elif isinstance(node, sem.Choices | sem.RoutedChoices):
        selected = (
            node.selected
            if isinstance(node, sem.RoutedChoices)
            else node.selection.value
            if isinstance(node.selection, Controlled)
            else ()
        )
        options = tuple(
            controls.Option(
                "".join(tree.plain(part) for part in resolve_text(item.label, context.localization).nodes),
                item.key,
                default=item.key in selected,
            )
            for item in node.choices
        )
        payload = SelectRealization(
            controls.RoutedSelect(
                options,
                node.route_id,
                "".join(tree.plain(part) for part in resolve_text(node.placeholder, context.localization).nodes)
                if node.placeholder is not None
                else None,
                node.minimum,
                node.maximum,
                not node.available,
            )
            if isinstance(node, sem.RoutedChoices)
            else controls.Select(options, node.key, min_values=node.minimum, max_values=node.maximum),
            navigation=True,
        )
        if isinstance(node, sem.Choices):
            binding = ActionBinding(
                node.key, SelectChoices(ChoiceCommit(node.selection, node.key, selected, context.presentation))
            )
    else:
        message = f"navigation {type(node).__name__}"
        raise UnsupportedSemanticError(message)
    return CandidateComposition(
        RealizationOffer[R, C, Realization](
            occurrence, "navigation", payload, tuple(child.offer.occurrence for child in children), binding=binding
        ),
        children,
    )


def region_cursor_key[RenderTargetT, ContextT](occurrence: SemanticOccurrence[RenderTargetT, ContextT]) -> str:
    """Keep layout-policy wrappers out of public region cursor paths."""
    if occurrence.origin is not None:
        return "$/" + "/".join(quote(key, safe="") for key in occurrence.page_source.keys[1:])
    parts: list[str] = []
    ref: SemanticRef | None = occurrence.reference
    while ref is not None:
        unit = occurrence.graph.units[ref]
        if (
            unit.parent is not None
            and not NativeTraits.of(unit.value).transparent
            and not isinstance(
                unit.value,
                sem.Budgeted
                | sem.KeepWithNext
                | sem.Unbreakable
                | sem.Truncated
                | sem.Spilled
                | sem.BestEffort
                | Budget
                | RootRegion,
            )
        ):
            parts.append(quote(ref.keys[-1], safe=""))
        ref = unit.parent
    return "$/" + "/".join(reversed(parts))


def control_key[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT], *, consume: Callable[[int], None] | None = None
) -> str:
    """Resolve an authored control's identity independently of its live callback."""
    sources = occurrence.sources
    if occurrence.origin is None and isinstance(
        occurrence.graph.units[occurrence.reference].value, sem.Navigation | sem.Items
    ):
        sources = frozenset({occurrence.reference})
    if len(sources) == 1 and occurrence.origin is None:
        source = next(iter(sources))
        key = source.keys[-1]
        source_key = key
        value = occurrence.graph.units[source].value
        parent = occurrence.graph.units[source].parent
        if parent is not None and isinstance(grid := occurrence.graph.units[parent].value, sem.Grid):
            key = f"{grid.key}.{key}"
        if isinstance(value, DisclosureToggle):
            source_key = value.details.key
            key = f"{source_key}.toggle"
        elif isinstance(value, NavigationChoice):
            source_key = value.navigation.key
            key = f"{source_key}.{value.option.key}"
        elif isinstance(value, ItemBack):
            source_key = value.items.key
            key = f"{source_key}.back"
        elif isinstance(value, sem.Items):
            key += ".focus"
        if consume is not None:
            consume(len(occurrence.graph.units))
        if (
            not key.startswith("@")
            and len(key) <= 64
            and sum(ref.keys[-1] in (source_key, key) for ref in occurrence.graph.units) == 1
        ):
            return key
    return grouped_control_key(occurrence)


def grouped_control_key[RenderTargetT, ContextT](occurrence: SemanticOccurrence[RenderTargetT, ContextT]) -> str:
    """Identify a grouped control from its complete semantic source scope."""
    encoded = json.dumps(sorted(ref.keys for ref in occurrence.sources), ensure_ascii=True)
    return "composition." + sha256(encoded.encode()).hexdigest()[:32]

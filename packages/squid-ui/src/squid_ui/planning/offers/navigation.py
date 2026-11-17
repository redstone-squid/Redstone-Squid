"""Build region navigation and stable identities for composed controls."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import cast
from urllib.parse import quote

from squid_i18n import tree
from squid_ui import scene
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Budget,
    SemanticRef,
)
from squid_ui.content.emoji import normalize_emoji
from squid_ui.content.text import TextLike, resolve_text
from squid_ui.interactions import ActionBinding
from squid_ui.interactions.forms import FormBinding
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
from squid_ui.planning.interaction_semantics import DisclosureToggle, ItemBack, NavigationChoice
from squid_ui.planning.pagination.content import RootRegion
from squid_ui.planning.pagination.navigation import PagerNav, default_nav
from squid_ui.planning.realizations import (
    ControlRealization,
    EntityRealization,
    PremiumRealization,
    Realization,
    RowRealization,
    SelectRealization,
    TextRealization,
)
from squid_ui.planning.semantic_index import UnsupportedSemanticError
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import (
    FIRST_PAGE,
    PageOwnership,
)


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
            controls = cast(
                tuple[primitive.Node[ContextT], ...],
                (() if navigation is None else navigation(self.cursor_key, page, pages, context.chrome, self.owner)),
            )
            if not self.disabled_controls:
                controls = tuple(
                    replace(node, items=tuple(item for item in node.items if not getattr(item, "disabled", False)))
                    if isinstance(node, primitive.Row)
                    else node
                    for node in controls
                )
            return (
                footer,
                *(
                    navigation_candidate(
                        node, replace(placed, identity=OccurrenceId((*placed.identity.path, str(index)))), context
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


def navigation_candidate[RenderTargetT, ContextT](
    node: primitive.Node[ContextT],
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    context: PlanningContext,
) -> CandidateComposition[RenderTargetT, ContextT, Realization]:
    """Expose navigation structure and bindings before its resource certification."""
    context.work.consume()

    def label(value: TextLike | None) -> str | None:
        return (
            None if value is None else "".join(tree.plain(n) for n in resolve_text(value, context.localization).nodes)
        )

    binding = None
    form: FormBinding[ContextT] | None = None
    children: tuple[CandidateComposition[RenderTargetT, ContextT, Realization], ...] = ()
    payload: Realization
    match node:
        case primitive.Row(items=items):
            payload = RowRealization()
            children = tuple(
                navigation_candidate(
                    child, replace(occurrence, identity=OccurrenceId((*occurrence.identity.path, str(index)))), context
                )
                for index, child in enumerate(items)
            )
        case primitive.Button():
            payload = ControlRealization(
                label(node.label) or "",
                action=node.key,
                style=node.style,
                disabled=node.disabled,
                mode=node.mode,
                emoji=normalize_emoji(node.emoji),
            )
            binding = ActionBinding(
                node.key,
                node.on_click,
                node.mode,
                guard=node.guard,
                busy=node.busy,
                label=node.label or "",
                record=node.record,
            )
            if isinstance(node, primitive.FormButton):
                form = node.form
        case primitive.RoutedButton():
            payload = ControlRealization(
                label(node.label) or "",
                route=node.route_id,
                style=node.style,
                disabled=node.disabled,
                emoji=normalize_emoji(node.emoji),
            )
        case primitive.PremiumButton():
            payload = PremiumRealization(node.sku_id)
        case primitive.LinkButton():
            payload = ControlRealization(
                label(node.label) or "", url=node.url, disabled=node.disabled, emoji=normalize_emoji(node.emoji)
            )
        case primitive.SelectMenu() | primitive.RoutedSelect():
            options = tuple(
                scene.Option(
                    label(option.label) or "",
                    option.value,
                    label(option.description),
                    option.default,
                    normalize_emoji(option.emoji),
                )
                for option in node.options
            )
            if isinstance(node, primitive.SelectMenu):
                payload = SelectRealization(
                    scene.Select(
                        options,
                        node.key,
                        label(node.placeholder),
                        node.min_values,
                        node.max_values,
                        node.disabled,
                        node.mode,
                    )
                )
                binding = ActionBinding(node.key, node.on_select, node.mode, routes=node.routes)
            else:
                payload = SelectRealization(
                    scene.RoutedSelect(
                        options, node.route_id, label(node.placeholder), node.min_values, node.max_values, node.disabled
                    )
                )
        case primitive.EntitySelect():
            payload = EntityRealization(
                scene.EntitySelect(
                    node.entity_type,
                    node.key,
                    label(node.placeholder),
                    node.default_values,
                    node.conversation_types,
                    node.min_values,
                    node.max_values,
                    node.disabled,
                    node.mode,
                )
            )
            binding = ActionBinding(node.key, node.on_select, node.mode)
        case _:
            msg = f"navigation {type(node).__name__}"
            raise UnsupportedSemanticError(msg)
    return CandidateComposition(
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "navigation",
            payload,
            tuple(child.offer.occurrence for child in children),
            binding=binding,
            form=form,
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
        if unit.parent is not None and not isinstance(
            unit.value,
            sem.Budgeted
            | primitive.Budget
            | primitive.Break
            | sem.KeepWithNext
            | sem.Unbreakable
            | sem.Truncated
            | sem.Spilled
            | sem.BestEffort
            | Budget
            | RootRegion,
        ):
            parts.append(quote(ref.keys[-1], safe=""))
        ref = unit.parent
    return "$/" + "/".join(reversed(parts))


def _control_key[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
) -> str:
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
        context.work.consume(len(occurrence.graph.units))
        if (
            not key.startswith("@")
            and len(key) <= 64
            and sum(ref.keys[-1] in (source_key, key) for ref in occurrence.graph.units) == 1
        ):
            return key
    encoded = json.dumps(sorted(ref.keys for ref in sources), ensure_ascii=True)
    return "composition." + sha256(encoded.encode()).hexdigest()[:32]

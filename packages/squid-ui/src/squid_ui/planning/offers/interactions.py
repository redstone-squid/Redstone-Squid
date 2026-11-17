"""Offer realizations and bindings for semantic interaction controls."""

from dataclasses import replace
from itertools import pairwise

from squid_i18n import tree
from squid_ui import scene
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.grids import coordinate
from squid_ui.authoring.presentations import (
    SemanticRef,
)
from squid_ui.capabilities import Capability
from squid_ui.content.entity import EntityRef
from squid_ui.content.text import resolve_text
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.interactions.forms import FormBinding
from squid_ui.interactions.ownership import Controlled
from squid_ui.planning.composition import (
    FragmentContract,
    OccurrenceId,
    Placement,
    RealizationOffer,
    RegionContract,
    SemanticOccurrence,
)
from squid_ui.planning.control_style import button_style as _button_style
from squid_ui.planning.interaction_handlers import (
    ChoiceCommit,
    CloseItem,
    EntityCommit,
    FlipToggle,
    FocusItem,
    GoToDestination,
    ItemCommit,
    NavigationCommit,
    PresentForm,
    SelectChoices,
    SelectDestination,
    SelectEntities,
    SelectEntityFallback,
    ToggleDetails,
)
from squid_ui.planning.interaction_semantics import DisclosureToggle, ItemBack, ItemSummary, NavigationChoice
from squid_ui.planning.offers.navigation import RegionNavigation, _control_key
from squid_ui.planning.offers.policy import Offers, _OfferScope
from squid_ui.planning.realizations import (
    ActionsRealization,
    ControlRealization,
    DisclosureRealization,
    EntityRealization,
    FlowRealization,
    Realization,
    ReferencedText,
    SelectRealization,
    TextRealization,
    TextSequenceRealization,
)
from squid_ui.planning.semantic_index import ItemBranch
from squid_ui.planning.structure import disclosure_state, navigation_current, opened_item, toggle_state
from squid_ui.primitives.constraints import (
    FIRST_PAGE,
)
from squid_ui.primitives.styles import ActionStyle


def _item_summary_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: ItemSummary[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "item-heading",
            TextRealization(
                resolve_text(value.item.label.content, context.localization).nodes, role="heading", level=3
            ),
        ),
    )


def _item_back_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: ItemBack[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    key = _control_key(occurrence, context)
    label = "".join(tree.plain(node) for node in resolve_text(context.chrome.back, context.localization).nodes)
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "item-back",
            ControlRealization(label, action=key),
            interaction=True,
            binding=ActionBinding(
                key, CloseItem(ItemCommit(value.items.opened, value.items.key, context.presentation))
            ),
        ),
    )


def _item_branch_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: ItemBranch[RenderTargetT, ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    refs = occurrence.graph.children.get(occurrence.reference, ())
    children = tuple(
        SemanticOccurrence(
            OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
            occurrence.graph,
            ref,
            Placement.HEADER if index == 0 and len(refs) > 1 else occurrence.placement,
            occurrence.origin,
            palette=context.palette,
            enclosing=occurrence.enclosing,
        )
        for index, ref in enumerate(refs)
    )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "item-detail", FlowRealization(), children, flow=True
        ),
    )


def _items_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: sem.Items[RenderTargetT, ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    opened = opened_item(value, context.presentation)
    refs = occurrence.graph.children.get(occurrence.reference, ())
    context.work.consume(len(refs))
    if opened is not None:
        visible = tuple(ref for ref in refs if ref.keys[-1] in (opened, "@back"))
        children = tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                Placement.FOOTER if ref.keys[-1] == "@back" else occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for ref in visible
        )
        cursor_key = f"{value.key}.{opened}.detail"
        return (
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                "items.opened",
                FlowRealization(),
                children,
                flow=True,
                region=RegionContract(
                    cursor_key,
                    RegionNavigation(
                        occurrence, scope.policy.disabled_controls, cursor_key=cursor_key, default_controls=True
                    ),
                    cursor_key=cursor_key,
                ),
            ),
        )
    if not value.items:
        return (
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence, "items.empty", FlowRealization(), flow=True
            ),
        )
    key = _control_key(occurrence, context)
    summaries: list[ReferencedText] = []
    item_options: list[scene.Option] = []
    for item, ref in zip(value.items, refs[:-1], strict=True):
        label = resolve_text(item.label.content, context.localization).nodes
        nodes = label
        if item.summary is not None:
            nodes += (tree.literal(" — "), *resolve_text(item.summary, context.localization).nodes)
        summaries.append(ReferencedText(SemanticRef((*ref.keys, "@summary")), nodes))
        item_options.append(scene.Option("".join(tree.plain(node) for node in label), item.key))
    payload = SelectRealization(scene.Select(tuple(item_options), key, "Choose an item"), summaries=tuple(summaries))
    cursor_key = f"{value.key}.items"
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "items.overview",
            payload,
            interaction=True,
            binding=ActionBinding(key, FocusItem(ItemCommit(value.opened, value.key, context.presentation))),
            fragments=FragmentContract(
                tuple(frozenset({summary.reference}) for summary in summaries),
                tuple(range(1, len(summaries) + 1)),
                payload.fragment,
            ),
            region=RegionContract(
                cursor_key,
                RegionNavigation(
                    occurrence, scope.policy.disabled_controls, cursor_key=cursor_key, default_controls=True
                ),
                cursor_key=cursor_key,
            ),
        ),
    )


def _disclosure_toggle_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: DisclosureToggle[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    details = value.details
    open_ = disclosure_state(details, context.presentation)
    key = _control_key(occurrence, context)
    label = "".join(tree.plain(node) for node in resolve_text(details.summary.content, context.localization).nodes)
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "disclosure-toggle",
            ControlRealization(label, action=key),
            interaction=True,
            binding=ActionBinding(key, ToggleDetails(details, open_, context.presentation)),
        ),
    )


def _details_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: sem.Details[RenderTargetT, ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    open_ = disclosure_state(value, context.presentation)
    refs = occurrence.graph.children.get(occurrence.reference, ())
    visible = refs if open_ else refs[:1]
    context.work.consume(len(visible))
    children = tuple(
        SemanticOccurrence(
            OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
            occurrence.graph,
            ref,
            Placement.HEADER if open_ and len(refs) > 1 and index == 0 else occurrence.placement,
            occurrence.origin,
            palette=context.palette,
            enclosing=occurrence.enclosing,
        )
        for index, ref in enumerate(visible)
    )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "disclosure", DisclosureRealization(open_), children, flow=True
        ),
    )


def _navigation_choice_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: NavigationChoice[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    navigation, option = value.navigation, value.option
    current = navigation_current(navigation, context.presentation)
    key = _control_key(occurrence, context)
    label = "".join(tree.plain(node) for node in resolve_text(option.label, context.localization).nodes)
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "destination",
            ControlRealization(
                label,
                action=key,
                style=ActionStyle.PRIMARY if option.key == current else ActionStyle.SECONDARY,
                current=option.key == current,
            ),
            interaction=True,
            binding=ActionBinding(
                key,
                GoToDestination(NavigationCommit(navigation.current, navigation.key, context.presentation), option.key),
                label=option.label,
            ),
        ),
    )


def _navigation_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: sem.Navigation[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    refs = occurrence.graph.children.get(occurrence.reference, ())
    available = tuple((ref, option) for ref, option in zip(refs, value.options, strict=True) if option.available)
    context.work.consume(len(refs))
    cursor_key = f"{value.key}.destinations"
    contract = RegionContract(
        cursor_key,
        RegionNavigation(occurrence, scope.policy.disabled_controls, cursor_key=cursor_key, default_controls=True),
        cursor_key=cursor_key,
    )
    individual = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "navigation.individual",
        ActionsRealization(navigation=True),
        tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for ref, _ in available
        ),
        flow=True,
        region=contract,
    )
    if not available:
        return (individual,)
    current = navigation_current(value, context.presentation)
    key = _control_key(occurrence, context)
    payload = SelectRealization(
        scene.Select(
            tuple(
                scene.Option(
                    "".join(tree.plain(node) for node in resolve_text(option.label, context.localization).nodes),
                    option.key,
                    default=option.key == current,
                )
                for _, option in available
            ),
            key,
        ),
        navigation=True,
    )
    grouped = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "navigation.grouped",
        payload,
        interaction=True,
        binding=ActionBinding(key, SelectDestination(NavigationCommit(value.current, value.key, context.presentation))),
        fragments=FragmentContract(
            tuple(frozenset({ref}) for ref, _ in available), tuple(range(1, len(available) + 1)), payload.fragment
        ),
        region=contract,
    )
    prefer_grouped = value.display == sem.NavigationDisplay.GROUPED or (
        value.display == sem.NavigationDisplay.AUTO and len(available) > 5
    )
    return (
        (grouped, replace(individual, preference=1)) if prefer_grouped else (individual, replace(grouped, preference=1))
    )


def _grid_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: sem.Grid[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    refs = occurrence.graph.children[occurrence.reference]
    children = tuple(
        SemanticOccurrence(
            OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
            occurrence.graph,
            ref,
            occurrence.placement,
            occurrence.origin,
            palette=context.palette,
            enclosing=occurrence.enclosing,
        )
        for ref in refs
    )
    contract = RegionContract(
        value.key + ".cells",
        RegionNavigation(occurrence, scope.policy.disabled_controls, owner=FIRST_PAGE, cursor_key=value.key + ".cells"),
        cursor_key=value.key + ".cells",
    )
    buttons = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "grid.buttons",
        ActionsRealization(columns=value.columns, grid_key=value.key),
        children,
        flow=True,
        region=contract,
        preference_priority=int(value.flexibility),
    )
    button_offers = (
        (buttons,)
        if (scope.policy.grid_columns is None or value.columns <= scope.policy.grid_columns)
        and (scope.policy.disabled_controls or all(cell.available for cell in value.cells))
        else ()
    )
    available = tuple(index for index, cell in enumerate(value.cells) if cell.available)
    labels = tuple(resolve_text(cell.label, context.localization).plain() for cell in value.cells)
    grid_summaries = tuple(
        ReferencedText(
            ref, (tree.literal(f"{coordinate(index, value.columns)} [{' ' if cell.available else 'x'}] {label}"),)
        )
        for index, (ref, cell, label) in enumerate(zip(refs, value.cells, labels, strict=True))
    )
    if not available:
        payload = TextSequenceRealization(grid_summaries)
        return (
            *button_offers,
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                "grid.readonly",
                payload,
                preference=1,
                fragments=FragmentContract(
                    tuple(frozenset({ref}) for ref in refs), tuple(range(1, len(refs) + 1)), payload.fragment
                ),
                region=contract,
                preference_priority=int(value.flexibility),
            ),
        )
    options = tuple(
        scene.Option(f"{coordinate(index, value.columns)} — {labels[index]}", value.cells[index].key)
        for index in available
    )
    action = _control_key(occurrence, context)
    # Attach unavailable cells to a neighbouring available cell. Every selector
    # fragment then retains its contextual labels and at least one valid option.
    boundaries = (0, *available[1:], len(refs))

    def grid_fragment(start: int, end: int) -> Realization:
        return SelectRealization(
            scene.Select(options[start:end], action), summaries=grid_summaries[boundaries[start] : boundaries[end]]
        )

    select = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "grid.coordinates",
        grid_fragment(0, len(available)),
        preference=1,
        fragments=FragmentContract(
            tuple(frozenset(refs[start:end]) for start, end in pairwise(boundaries)),
            tuple(range(1, len(available) + 1)),
            grid_fragment,
        ),
        region=contract,
        preference_priority=int(value.flexibility),
        interaction=True,
        binding=ActionBinding(action, value.on_pick),
    )
    return (*button_offers, select)


def _entities_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: sem.Entities[ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    previous = (
        value.selection.value
        if isinstance(value.selection, Controlled)
        else tuple(
            EntityRef.decode(key)
            for key in context.presentation.selection(
                value.key, initial=tuple(ref.encode() for ref in value.selection.initial)
            ).selected
        )
    )
    key = _control_key(occurrence, context)
    commit = EntityCommit(value.selection, value.key, previous, context.presentation)
    options = tuple(
        scene.Option(
            "".join(tree.plain(node) for node in resolve_text(choice.label, context.localization).nodes),
            choice.ref.encode(),
            "".join(tree.plain(node) for node in resolve_text(choice.description, context.localization).nodes)
            if choice.description is not None
            else None,
            choice.ref in previous,
        )
        for choice in value.choices
        if choice.available
    )
    placeholder = (
        "".join(tree.plain(node) for node in resolve_text(value.placeholder, context.localization).nodes)
        if value.placeholder is not None
        else None
    )
    native = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "entity",
        EntityRealization(
            scene.EntitySelect(
                value.entity_type,
                key,
                placeholder,
                previous,
                value.conversation_types,
                value.minimum,
                value.maximum,
            ),
            options,
        ),
        interaction=True,
        binding=ActionBinding(key, SelectEntities(commit), ActionMode.EXCLUSIVE),
    )
    if not value.choices:
        return (native,)
    by_key = {choice.ref.encode(): choice.ref for choice in value.choices if choice.available}
    selected = tuple(ref.encode() for ref in previous if ref.encode() in by_key)
    fallback = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "entity-choices",
        SelectRealization(scene.Select(options, key, placeholder, value.minimum, value.maximum)),
        preference=1,
        interaction=True,
        binding=ActionBinding(
            key,
            SelectChoices(
                ChoiceCommit(
                    Controlled(selected, SelectEntityFallback(commit, by_key)),
                    value.key,
                    selected,
                    context.presentation,
                )
            ),
            ActionMode.EXCLUSIVE,
        ),
    )
    return (native, fallback)


def _choices_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: sem.Choices[ContextT] | sem.RoutedChoices
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    previous = (
        value.selected
        if isinstance(value, sem.RoutedChoices)
        else value.selection.value
        if isinstance(value.selection, Controlled)
        else context.presentation.selection(value.key, initial=value.selection.initial).selected
    )
    options = tuple(
        scene.Option(
            "".join(tree.plain(node) for node in resolve_text(choice.label, context.localization).nodes),
            choice.key,
            "".join(tree.plain(node) for node in resolve_text(choice.description, context.localization).nodes)
            if choice.description is not None
            else None,
            choice.key in previous,
        )
        for choice in value.choices
        if choice.available
    )
    key = _control_key(occurrence, context)
    if isinstance(value, sem.RoutedChoices):
        placeholder = (
            "".join(tree.plain(node) for node in resolve_text(value.placeholder, context.localization).nodes)
            if value.placeholder is not None
            else None
        )
        payload = SelectRealization(
            scene.RoutedSelect(options, value.route_id, placeholder, value.minimum, value.maximum, not value.available)
        )
        binding = None
    else:
        payload = SelectRealization(scene.Select(options, key, min_values=value.minimum, max_values=value.maximum))
        binding = ActionBinding(
            key,
            SelectChoices(ChoiceCommit(value.selection, value.key, previous, context.presentation)),
            ActionMode.EXCLUSIVE,
        )
    offer = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence, "select", payload, interaction=True, binding=binding
    )
    if isinstance(value, sem.Choices) and value.maximum == 1 and options:
        cursor_key = f"{value.key}.choices"
        offer = replace(
            offer,
            fragments=FragmentContract(
                (frozenset({occurrence.reference}),) * len(options),
                tuple(range(1, len(options) + 1)),
                payload.fragment,
                continuations=True,
            ),
            region=RegionContract(
                cursor_key,
                RegionNavigation(
                    occurrence, scope.policy.disabled_controls, cursor_key=cursor_key, default_controls=True
                ),
                cursor_key=cursor_key,
            ),
        )
    return (offer,)


def _action_control_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT],
    value: sem.ActionControl[ContextT]
    | sem.RoutedActionControl
    | sem.Link
    | sem.FormTrigger[ContextT]
    | sem.Toggle[ContextT],
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    # Wire IDs follow semantic ownership, independently of presentation keys.
    key = _control_key(occurrence, context)
    label = "".join(tree.plain(node) for node in resolve_text(value.label, context.localization).nodes)
    binding = None
    form: FormBinding[ContextT] | None = None
    match value:
        case sem.Toggle():
            on = toggle_state(value, context.presentation)
            state_label = value.on_label if on else value.off_label
            if state_label is None:
                state_label = context.chrome.on if on else context.chrome.off
            label += ": " + "".join(tree.plain(node) for node in resolve_text(state_label, context.localization).nodes)
            payload = ControlRealization(
                label,
                action=key,
                style=_button_style(value.tone, sem.Emphasis.NORMAL),
                disabled=not value.available,
            )
            binding = ActionBinding(key, FlipToggle(value, on, context.presentation), ActionMode.EXCLUSIVE, label=label)
        case sem.ActionControl():
            payload = ControlRealization(
                label,
                action=key,
                style=_button_style(value.tone, value.emphasis),
                disabled=not value.available,
                mode=value.mode,
            )
            binding = ActionBinding(
                key,
                value.on_trigger,
                value.mode,
                guard=value.guard,
                busy=value.busy,
                label=value.label,
                record=value.record,
            )
        case sem.FormTrigger():
            if not {Capability.FORMS_MODAL, Capability.FORMS_INLINE}.intersection(context.capabilities):
                return ()
            spec = value.spec.adapt(context.capabilities)
            form = FormBinding(key, spec, value.on_submit, value.mode, value.label, value.record)
            binding = ActionBinding(
                key,
                PresentForm(spec, key, value.on_submit, value.mode, value.label, value.record),
                value.mode,
                guard=value.guard,
                label=value.label,
            )
            payload = ControlRealization(
                label, action=key, style=_button_style(value.tone, value.emphasis), mode=value.mode
            )
        case sem.RoutedActionControl():
            payload = ControlRealization(
                label,
                route=value.route_id,
                style=_button_style(value.tone, value.emphasis),
                disabled=not value.available,
            )
        case sem.Link():
            payload = ControlRealization(label, url=value.url)
    parent = occurrence.graph.units[occurrence.reference].parent
    keep_next = False
    if parent is not None and isinstance(grid := occurrence.graph.units[parent].value, sem.Grid):
        index = occurrence.graph.children[parent].index(occurrence.reference)
        keep_next = (index + 1) % grid.columns != 0 and index + 1 < len(grid.cells)
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "button", payload, interaction=True, binding=binding, form=form, keep_with_next=keep_next
        ),
    )

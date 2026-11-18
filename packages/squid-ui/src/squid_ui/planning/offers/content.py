"""Offer realizations for semantic content and layout containers."""

from dataclasses import replace
from datetime import UTC

from squid_i18n import tree
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Alternatives,
    Budget,
    Omission,
    Replacement,
    SemanticRef,
    TextPresentation,
)
from squid_ui.content.text import resolve_text, timestamp_text
from squid_ui.errors import LayoutInvariantError
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.native import NativeTraits, NativeValue
from squid_ui.planning import controls
from squid_ui.planning.composition import (
    FragmentContract,
    OccurrenceId,
    Placement,
    RealizationOffer,
    RegionContract,
    SemanticOccurrence,
)
from squid_ui.planning.interaction_handlers import (
    RouteSelection,
)
from squid_ui.planning.offers.navigation import grouped_control_key, pagination_chrome, region_cursor_key
from squid_ui.planning.offers.policy import Offers, OfferScope
from squid_ui.planning.pagination.content import RootRegion
from squid_ui.planning.realizations import (
    ActionsRealization,
    DownloadRealization,
    FieldRealization,
    FieldsRealization,
    FigureRealization,
    FlowRealization,
    ImageRealization,
    MetricRealization,
    PanelRealization,
    ProgressRealization,
    Realization,
    ReferencedText,
    SelectRealization,
    StatusRealization,
    TableForm,
    TableRealization,
    TableRecord,
    TextRealization,
    TextSequenceRealization,
    TimeRealization,
)
from squid_ui.planning.semantic_correspondence import SourceScope, check_alternative
from squid_ui.planning.semantic_index import RosterLabel, RosterMembers, RosterSection, StructuralGroup
from squid_ui.planning.structure import resolve_accent
from squid_ui.planning.text_presentations import (
    UnsupportedText,
    resolve_presentation_text,
    text_ends,
    text_fragments,
)


def _fallback_content_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.FallbackContent[RenderTargetT, ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    refs = occurrence.graph.children[occurrence.reference]
    primary = refs[0]
    sources = (
        occurrence.origin.references(primary) if occurrence.origin is not None else occurrence.graph.atoms(primary)
    )
    context.work.consume(len(sources) + len(refs))
    effects = tuple(Replacement(source) for source in sorted(sources or {primary}, key=lambda ref: ref.keys))
    return tuple(
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            f"fallback-{index}",
            FlowRealization(),
            (
                SemanticOccurrence(
                    OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                    occurrence.graph,
                    ref,
                    occurrence.placement,
                    occurrence.origin,
                    palette=context.palette,
                    enclosing=occurrence.enclosing,
                ),
            ),
            effects=effects if index else (),
            preference=index,
            flow=True,
        )
        for index, ref in enumerate(refs)
    )


def _optional_content_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.OptionalContent[RenderTargetT, ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    ref = occurrence.graph.children[occurrence.reference][0]
    child = SemanticOccurrence(
        OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
        occurrence.graph,
        ref,
        occurrence.placement,
        occurrence.origin,
        palette=context.palette,
        enclosing=occurrence.enclosing,
    )
    sources = sorted(occurrence.sources, key=lambda source: source.keys)
    context.work.consume(len(sources))
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "retain", FlowRealization(), (child,), flow=True, preference_priority=value.importance
        ),
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "omit",
            FlowRealization(),
            effects=tuple(Omission(source) for source in sources),
            preference=1,
            flow=True,
            preference_priority=value.importance,
        ),
    )


def _alternatives_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: Alternatives[RenderTargetT, ContextT]
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    choices: list[RealizationOffer[RenderTargetT, ContextT, Realization]] = []
    for choice in value.choices:
        context.work.consume()
        if not choice.requires <= context.capabilities:
            continue
        checked = check_alternative(value, choice, consume=context.work.consume)
        origin = SourceScope(checked, occurrence.reference, occurrence.origin)
        root = next(ref for ref, unit in checked.presentation.units.items() if unit.parent is None)
        child = SemanticOccurrence(
            OccurrenceId((*occurrence.identity.path, "@presentation")),
            checked.presentation,
            root,
            occurrence.placement,
            origin,
            palette=context.palette,
            enclosing=occurrence,
        )
        effects = tuple(
            replace(effect, source=source)
            for effect in choice.fidelity.effects
            for source in sorted(
                origin.source_references(checked.source.resolve(effect.source)), key=lambda ref: ref.keys
            )
        )
        choices.append(
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                choice.key,
                FlowRealization(),
                (child,),
                effects,
                choice.preference,
                flow=True,
                preference_priority=value.priority,
            )
        )
    return tuple(choices)


def _roster_label_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: RosterLabel
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if value.full:
        label_nodes = resolve_text(context.chrome.full, context.localization).nodes
    elif value.section.slot is None:
        label_nodes = resolve_text(context.chrome.waitlist, context.localization).nodes
    else:
        label_nodes = (
            *resolve_text(value.section.slot.label, context.localization).nodes,
            tree.literal(" — "),
            *resolve_text(
                context.chrome.slot_count(value.section.count, value.section.slot.capacity), context.localization
            ).nodes,
        )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "roster-label",
            TextRealization(label_nodes, role="paragraph" if value.full else "heading", level=3),
        ),
    )


def _list_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.List
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    items = tuple(
        ReferencedText(
            ref,
            resolve_text(item.content, context.localization).nodes,
            (f"{index + 1}. " if value.ordered else "• ") if value.markers else "",
            ordinal=index + 1,
        )
        for index, (ref, item) in enumerate(
            zip(occurrence.graph.children.get(occurrence.reference, ()), value.items, strict=True)
        )
    )
    per = value.group_size or value.page_size
    required: list[int] = []
    expanded: list[ReferencedText] = []
    capacity = scope.text_capacity
    for index, item in enumerate(items):
        plain = "".join(tree.plain(node) for node in item.nodes)
        if capacity is not None and len(plain) > capacity // 2:
            size = max(1, min(256, capacity // 8))
            context.work.consume(1 + len(plain) // size)
            expanded.extend(
                replace(
                    item,
                    nodes=nodes,
                    prefix=item.prefix if index == 0 else "",
                    continuation=index > 0,
                )
                for index, nodes in enumerate(text_fragments(item.nodes, size))
            )
        else:
            expanded.append(item)
        if per is not None and (index + 1) % per == 0:
            required.append(len(expanded))
    items = tuple(expanded)
    payload = TextSequenceRealization(items, list_ordered=value.ordered if value.markers else None)
    units = tuple(frozenset({item.reference}) for item in items)
    legal_ends = (
        tuple(dict.fromkeys((*required, len(items))))
        if value.group_size is not None
        else tuple(range(1, len(items) + 1))
    )
    fragments = FragmentContract(units, legal_ends, payload.fragment, continuations=True) if items else None
    contract = RegionContract(
        value.key,
        pagination_chrome(occurrence, scope.context, scope.policy.disabled_controls),
        owner=value.page,
        cursor_key=value.key,
        required_ends=tuple(required),
    )
    complete = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "member-lines" if isinstance(value, RosterMembers) else "text-pages",
        payload,
        fragments=fragments,
        region=None if isinstance(value, RosterMembers) else contract,
    )
    if value.group_size is not None or (not scope.spill and not isinstance(value, RosterMembers)) or not value.items:
        return (complete,)
    originals = tuple(
        ReferencedText(
            ref,
            resolve_text(item.content, context.localization).nodes,
            (f"{index + 1}. " if value.ordered else "• ") if value.markers else "",
            ordinal=index + 1,
        )
        for index, (ref, item) in enumerate(
            zip(occurrence.graph.children[occurrence.reference], value.items, strict=True)
        )
    )
    order = sorted(range(len(originals)), key=lambda index: (value.items[index].importance, -index))
    spilled_lists = [complete]
    for count in range(1, len(originals) + 1):
        context.work.consume(len(originals))
        omitted = set(order[:count])
        retained = tuple(item for index, item in enumerate(originals) if index not in omitted)
        shortened_sequence = replace(
            payload,
            items=retained,
            notice=resolve_text(context.chrome.and_n_more(count), context.localization).nodes,
        )
        spilled_lists.append(
            replace(
                complete,
                key=f"spill-{count}",
                payload=shortened_sequence,
                preference=count,
                effects=occurrence.scope_effects(
                    tuple(Omission(item.reference) for index, item in enumerate(originals) if index in omitted)
                ),
                fragments=FragmentContract(
                    tuple(frozenset({item.reference}) for item in retained),
                    tuple(range(1, len(retained) + 1)),
                    shortened_sequence.fragment,
                )
                if retained
                else None,
                region=replace(
                    contract, required_ends=tuple(range(per, len(retained) + 1, per)) if per is not None else ()
                )
                if not isinstance(value, RosterMembers)
                else None,
            )
        )
    spilled_lists.append(
        replace(
            spilled_lists[-1],
            key="spill-empty",
            payload=replace(payload, items=(), notice=()),
            preference=len(originals) + 1,
        )
    )
    return tuple(spilled_lists)


def _text_presentation_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: TextPresentation
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if occurrence.origin is None:
        message = "source-referenced text must belong to a checked alternative"
        raise LayoutInvariantError(message)
    resolved = resolve_presentation_text(
        occurrence.origin.checked, occurrence.reference, localization=context.localization
    )
    if isinstance(resolved, UnsupportedText):
        return ()
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "text", TextRealization(resolved.nodes, resolved)
        ),
    )


def _metric_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Metric
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    label = resolve_text(value.label, context.localization).nodes
    rendered = f"{value.value}{' ' + value.unit if value.unit is not None else ''}"
    nodes = (
        tree.Node("strong", children=(*label, tree.literal(":")), tag="strong", markup="**"),
        tree.literal(" " + rendered),
    )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "metric", MetricRealization(nodes, label=label, value=rendered)
        ),
    )


def _timestamp_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT],
    value: sem.Timestamp | sem.ZonedTimestamp,
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    label = (
        resolve_text(value.label, context.localization).nodes
        if isinstance(value, sem.Timestamp | sem.ZonedTimestamp) and value.label is not None
        else ()
    )
    if isinstance(value, sem.Timestamp):
        instant = value.instant
        style = sem.TimeStyle(value.style).value
        timestamp = timestamp_text(instant, style=style)
        time_nodes = resolve_text(timestamp, context.localization).nodes
        display = instant.isoformat()
        timezone = None
    else:
        instant = value.value.instant
        style = None
        display = value.value.isoformat()
        time_nodes = (tree.literal(display),)
        timezone = value.value.timezone
    prefix = (
        (tree.Node("strong", children=(*label, tree.literal(":")), tag="strong", markup="**"), tree.literal(" "))
        if label
        else ()
    )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "time",
            TimeRealization(
                (*prefix, *time_nodes),
                instant=instant.astimezone(UTC).isoformat(),
                display=display,
                style=style,
                timezone=timezone,
                label=label,
                prefix=prefix,
                native_prefix=None,
            ),
        ),
    )


def _status_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Status
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    nodes = (tree.literal(value.tone.status_marker), *resolve_text(value.content, context.localization).nodes)
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "status",
            StatusRealization(
                nodes,
                colour=context.palette.tone(value.tone),
                tone=value.tone.base.value,
                emphasis=value.emphasis.value,
            ),
        ),
    )


def _progress_bar_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.ProgressBar
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    label = (
        "".join(tree.plain(node) for node in resolve_text(value.label, context.localization).nodes)
        if value.label is not None
        else None
    )
    # Text-only targets retain the authored quantities, not just a rounded percentage.
    text = f"{label}: " if label is not None else ""
    ratio = 0.0 if value.maximum <= 0 else max(0.0, min(1.0, value.value / value.maximum))
    filled = round(ratio * 10)
    text += f"{value.value}/{value.maximum} {'█' * filled}{'░' * (10 - filled)} {ratio:.0%}"
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "progress",
            ProgressRealization((tree.literal(text),), value=value.value, maximum=value.maximum, label=label),
        ),
    )


def _text_block_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Heading | sem.Note | sem.Code | sem.Quote
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    payload = TextRealization(resolve_text(value.content, context.localization).nodes)
    if isinstance(value, sem.Heading):
        payload = replace(payload, role="heading", level=value.level)
    elif isinstance(value, sem.Note):
        # Empty localized notes have no visible content or layout cost. In particular,
        # they must not become a standalone Discord subtext marker.
        if not any(tree.plain(node).strip() for node in payload.nodes):
            return (
                RealizationOffer[RenderTargetT, ContextT, Realization](
                    occurrence, "empty-note", FlowRealization(), flow=True
                ),
            )
        payload = replace(payload, role="note")
    elif isinstance(value, sem.Code):
        payload = replace(payload, role="code", language=value.language)
    else:
        if value.attribution is not None:
            payload = replace(
                payload,
                nodes=(
                    *payload.nodes,
                    tree.literal(" — "),
                    *resolve_text(value.attribution, context.localization).nodes,
                ),
            )
        payload = replace(payload, role="quote")
    return scope.text_offers(
        RealizationOffer[RenderTargetT, ContextT, Realization](occurrence, "text", payload),
        payload,
        priority=value.importance if isinstance(value, sem.Heading | sem.Note) else sem.Importance.NORMAL,
    )


def _field_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Field
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    label = resolve_text(value.label, context.localization).nodes
    effects = occurrence.scope_effects((Replacement(SemanticRef((*occurrence.reference.keys, "value"))),))
    context.work.consume(1 + len(value.fallbacks))
    return tuple(
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "field" if index == 0 else f"field-fallback-{index}",
            FieldRealization((*label, tree.literal(": "), *nodes), label=label, value=nodes),
            effects=effects if index else (),
            preference=index,
            preference_priority=value.importance if value.fallbacks else None,
        )
        for index, content in enumerate((value.value, *value.fallbacks))
        for nodes in (resolve_text(content, context.localization).nodes,)
    )


def _paragraph_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Paragraph
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    payload = TextRealization(resolve_text(value.content, context.localization).nodes)
    length = sum(len(tree.plain(node)) for node in payload.nodes)
    fragments = None
    if scope.text_capacity is not None and (
        length > scope.text_capacity or length > max(128, scope.text_capacity // 2)
    ):
        context.work.consume(length)
        fragments = FragmentContract(
            (frozenset({occurrence.reference}),) * length,
            text_ends(payload.nodes),
            payload.fragment,
            continuations=True,
        )
    return scope.text_offers(
        RealizationOffer[RenderTargetT, ContextT, Realization](occurrence, "text", payload, fragments=fragments),
        payload,
        priority=value.importance,
    )


def container_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT],
    value: sem.Group[RenderTargetT, ContextT]
    | sem.Roster[ContextT]
    | RosterSection
    | sem.Stack[RenderTargetT, ContextT]
    | sem.Section[RenderTargetT, ContextT]
    | sem.Article[RenderTargetT, ContextT]
    | sem.Aside[RenderTargetT, ContextT]
    | sem.Fields
    | sem.Media
    | sem.Figure
    | sem.Cluster[RenderTargetT, ContextT]
    | sem.Themed[RenderTargetT, ContextT]
    | sem.ActionControls[ContextT]
    | sem.ControlGroup[ContextT]
    | sem.Truncated[RenderTargetT, ContextT]
    | sem.Spilled[RenderTargetT, ContextT]
    | sem.BestEffort[RenderTargetT, ContextT]
    | sem.Region[RenderTargetT, ContextT]
    | sem.Paged[RenderTargetT, ContextT]
    | sem.Budgeted[RenderTargetT, ContextT]
    | NativeValue
    | StructuralGroup
    | sem.Block[RenderTargetT, ContextT]
    | Budget[RenderTargetT, ContextT]
    | sem.Unbreakable[RenderTargetT, ContextT]
    | sem.KeepWithNext[RenderTargetT, ContextT],
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    refs = list(occurrence.graph.children.get(occurrence.reference, ()))
    context.work.consume(len(refs))
    if isinstance(value, sem.Region):
        refs.sort(key=lambda ref: -1 if ref.keys[-1] == "@header" else 1 if ref.keys[-1] == "@footer" else 0)
    placement = occurrence.placement
    if isinstance(value, StructuralGroup):
        if value.part == "header":
            placement = Placement.HEADER
        elif value.part == "footer":
            placement = Placement.FOOTER
    children = tuple(
        SemanticOccurrence(
            OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
            occurrence.graph,
            ref,
            Placement.HEADER
            if (isinstance(value, RootRegion) and NativeTraits.of(occurrence.graph.units[ref].value).content_axis)
            or (isinstance(value, sem.Region) and ref.keys[-1] == "@header")
            or (isinstance(value, sem.Section | sem.Article | RosterSection) and ref.keys[-1] == "@heading")
            else Placement.FOOTER
            if (isinstance(value, sem.Region) and ref.keys[-1] == "@footer")
            or (isinstance(value, RosterSection) and ref.keys[-1] != "@members")
            else placement,
            occurrence.origin,
            palette=context.palette,
            enclosing=occurrence.enclosing,
        )
        for ref in refs
    )
    if isinstance(value, sem.Section | sem.Article):
        payload = PanelRealization(
            resolve_accent(value.accent, context.palette),
            framed=scope.policy.panels and (scope.policy.nested_panels or not scope.inside_panel),
            thumbnail=value.thumbnail,
            heading="".join(
                tree.plain(node) for node in resolve_text(value.heading.content, context.localization).nodes
            ),
            kind="section" if isinstance(value, sem.Section) else "article",
        )
    elif isinstance(value, sem.Aside):
        payload = PanelRealization(
            context.palette.tone(value.tone),
            framed=scope.policy.panels and (scope.policy.nested_panels or not scope.inside_panel),
            kind="aside",
            tone=value.tone.base.value,
        )
    elif isinstance(value, sem.Block):
        payload = PanelRealization(
            resolve_accent(value.accent, context.palette),
            framed=scope.policy.panels and (scope.policy.nested_panels or not scope.inside_panel),
        )
    elif isinstance(value, sem.ActionControls | sem.ControlGroup):
        payload = ActionsRealization()
    elif isinstance(value, sem.Fields):
        payload = FieldsRealization()
    elif isinstance(value, sem.Figure):
        payload = FigureRealization(value.caption is not None)
    else:
        payload = FlowRealization()
    region = None
    if isinstance(value, sem.Region | sem.Paged):
        nested_region = any(
            isinstance(ancestor, sem.Region) and not isinstance(ancestor, RootRegion)
            for ancestor in occurrence.ancestors()
        )
        cursor_key = (
            region_cursor_key(occurrence)
            if (isinstance(value, sem.Region) or nested_region) and not isinstance(value, RootRegion)
            else value.key
        )
        region = RegionContract(
            value.key,
            pagination_chrome(
                occurrence,
                scope.context,
                scope.policy.disabled_controls,
                value.footer if isinstance(value, sem.Paged) else None,
            ),
            value.chars if isinstance(value, sem.Paged) else None,
            value.min_fill if isinstance(value, sem.Paged) else 0,
            value.widows if isinstance(value, sem.Paged) else 1,
            value.page,
            cursor_key,
        )
    container = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence,
        "container",
        payload,
        children,
        flow=not (
            isinstance(value, sem.Unbreakable | sem.Figure)
            or (isinstance(value, RosterSection) and value.count == 0)
            or NativeTraits.of(value).unbreakable
        ),
        keep_with_next=isinstance(value, sem.KeepWithNext) or NativeTraits.of(value).keep_with_next,
        region=region,
        preference_priority=int(value.flexibility) if isinstance(value, sem.ActionControls) else None,
    )
    if isinstance(value, sem.Fields) and scope.spill and value.fields:
        order = sorted(range(len(children)), key=lambda index: (value.fields[index].importance, -index))
        spilled_fields = [container]
        for count in range(1, len(children) + 1):
            context.work.consume(len(children))
            omitted = set(order[:count])
            spilled_fields.append(
                replace(
                    container,
                    key=f"spill-{count}",
                    children=tuple(child for index, child in enumerate(children) if index not in omitted),
                    payload=FieldsRealization(
                        resolve_text(context.chrome.and_n_more(count), context.localization).nodes
                    ),
                    preference=count,
                    effects=occurrence.scope_effects(
                        tuple(Omission(child.reference) for index, child in enumerate(children) if index in omitted)
                    ),
                )
            )
        spilled_fields.append(
            replace(spilled_fields[-1], key="spill-empty", payload=FieldsRealization(), preference=len(children) + 1)
        )
        return tuple(spilled_fields)
    if isinstance(value, sem.ActionControls) and value.display != sem.ControlDisplay.INDIVIDUAL:
        eligible = tuple(
            item
            for item in value.items
            if isinstance(item, sem.ActionControl)
            and item.available
            and (
                item.allow_grouping
                if item.allow_grouping is not None
                else item.emphasis != sem.Emphasis.STRONG and item.tone.base in (sem.Tone.NEUTRAL, sem.Tone.INFO)
            )
        )
        if eligible and (len(eligible) > 1 or value.display == sem.ControlDisplay.GROUPED):
            key = grouped_control_key(occurrence)
            routes = {
                item.key: ActionBinding(
                    item.key,
                    item.on_trigger,
                    item.mode,
                    guard=item.guard,
                    busy=item.busy,
                    label=item.label,
                    record=item.record,
                )
                for item in eligible
            }
            options = tuple(
                controls.Option(
                    "".join(tree.plain(node) for node in resolve_text(item.label, context.localization).nodes),
                    item.key,
                )
                for item in eligible
            )
            grouped = RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                "actions.grouped",
                SelectRealization(controls.Select(options, key)),
                children=tuple(child for child in children if child.graph.units[child.reference].value not in eligible),
                interaction=True,
                binding=ActionBinding(key, RouteSelection(routes), ActionMode.EXCLUSIVE, routes=routes),
                preference=0 if value.display == sem.ControlDisplay.GROUPED else 1,
                preference_priority=int(value.flexibility),
            )
            if len(value.items) > 25:
                cursor_key = f"{value.key}.actions"
                contract = RegionContract(
                    cursor_key,
                    pagination_chrome(occurrence, scope.context, scope.policy.disabled_controls, default_controls=True),
                    cursor_key=cursor_key,
                )
                container = replace(container, region=contract)
                if len(eligible) > 25 and len(eligible) != len(value.items):
                    return (container,)
                if len(eligible) == len(value.items):
                    payload = SelectRealization(controls.Select(options, key))
                    grouped = replace(
                        grouped,
                        fragments=FragmentContract(
                            tuple(child.sources for child in children),
                            tuple(range(1, len(eligible) + 1)),
                            payload.fragment,
                        ),
                        region=contract,
                    )
            return (
                (replace(container, preference=1), grouped)
                if value.display == sem.ControlDisplay.GROUPED
                else (container, grouped)
            )
    return (container,)


def _table_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Table
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    table_forms = scope.policy.table_forms
    context.work.consume(len(value.columns.columns))
    prefix = occurrence.reference.keys
    columns = tuple(
        ReferencedText(
            SemanticRef((*prefix, "columns", column.key or f"@{i}")),
            resolve_text(column.heading, context.localization).nodes,
        )
        for i, column in enumerate(value.columns.columns)
    )
    rows: list[TableRecord] = []
    for i, row in enumerate(value.rows):
        context.work.consume(1 + len(row.cells))
        row_ref = SemanticRef((*prefix, "rows", row.key or f"@{i}"))
        cells = tuple(
            ReferencedText(
                SemanticRef((*row_ref.keys, column.reference.keys[-1])),
                resolve_text(cell, context.localization).nodes,
            )
            for column, cell in zip(columns, row.cells, strict=True)
        )
        rows.append(TableRecord(row_ref, cells))
    preferred = table_forms[0] if table_forms else None
    if value.display == sem.TableDisplay.TABULAR:
        preferred = TableForm.NATIVE
    elif value.display == sem.TableDisplay.RECORDS:
        preferred = TableForm.RECORDS
    elif value.display == sem.TableDisplay.MATRIX:
        preferred = TableForm.MATRIX
        table_forms = (TableForm.MATRIX,) if TableForm.MATRIX in table_forms else ()
    offers: list[RealizationOffer[RenderTargetT, ContextT, Realization]] = []
    for index, form in enumerate(table_forms):
        payload = TableRealization(columns, tuple(rows), form)
        units = tuple(frozenset(cell.reference for cell in row.cells) for row in rows)
        repeated = frozenset(column.reference for column in columns)
        fragments = (
            FragmentContract(units, tuple(range(1, len(rows) + 1)), payload.fragment, repeated) if rows else None
        )
        offers.append(
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                form.value,
                payload,
                preference=0 if form == preferred else index + 1,
                fragments=fragments,
            )
        )
    return tuple(offers)


def _media_item_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.MediaItem
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    description = (
        "".join(tree.plain(node) for node in resolve_text(value.description, context.localization).nodes)
        if value.description is not None
        else ""
    )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "image", ImageRealization(value.source, description, value.spoiler)
        ),
    )


def _download_offers[RenderTargetT, ContextT](
    scope: OfferScope[RenderTargetT, ContextT], value: sem.Download
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    payload = DownloadRealization(
        value.asset,
        resolve_text(value.label if value.label is not None else context.chrome.download, context.localization).nodes,
        resolve_text(value.description, context.localization).nodes if value.description is not None else (),
        value.spoiler,
    )
    return (RealizationOffer[RenderTargetT, ContextT, Realization](occurrence, "download", payload),)

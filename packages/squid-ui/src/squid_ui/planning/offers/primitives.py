"""Offer realizations for explicitly authored primitive nodes."""

from dataclasses import replace
from typing import cast

from squid_i18n import tree
from squid_ui import scene
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    FidelityEffect,
    Omission,
    Replacement,
    Shortening,
)
from squid_ui.content.text import resolve_text
from squid_ui.planning.composition import (
    FragmentContract,
    OccurrenceId,
    OfferDiscovery,
    RealizationOffer,
    RegionContract,
    SemanticOccurrence,
)
from squid_ui.planning.offers.navigation import RegionNavigation, _control_key, navigation_candidate, region_cursor_key
from squid_ui.planning.offers.policy import Offers, _OfferScope
from squid_ui.planning.realizations import (
    ActionsRealization,
    CardRealization,
    ClassicContentRealization,
    FileRealization,
    FlowRealization,
    GalleryRealization,
    NativeSectionRealization,
    PanelRealization,
    Realization,
    ReferencedText,
    RowRealization,
    SeparatorRealization,
    TextRealization,
    TextSequenceRealization,
    ThumbnailRealization,
)
from squid_ui.planning.semantic_index import CardSlot
from squid_ui.planning.text_presentations import (
    _slice,
    text_ends,
    text_fragments,
)
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import (
    Alt,
    Alts,
    Condense,
    Drop,
    Never,
    Paginate,
    Spill,
    Truncate,
)


def _lines_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: primitive.Lines
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    overflow = Spill() if scope.spill else Truncate(scope.keep) if scope.keep is not None else value.overflow
    if not scope.policy.native_controls or not isinstance(
        overflow, Never | Truncate | Spill | Drop | Condense | Paginate
    ):
        return ()
    items = tuple(
        ReferencedText(ref, resolve_text(item.content, context.localization).nodes)
        for ref in occurrence.graph.children.get(occurrence.reference, ())
        for item in (occurrence.graph.units[ref].value,)
        if isinstance(item, sem.ListItem)
    )
    line_separator = value.join

    def line_payload(lines: tuple[ReferencedText, ...]) -> TextRealization:
        return TextRealization(
            tuple(
                node
                for index, item in enumerate(lines)
                for node in (
                    *((tree.literal(line_separator),) if index else ()),
                    *item.nodes,
                )
            )
        )

    payload = line_payload(items)
    line_priority = value.priority
    pagination = overflow if isinstance(overflow, Paginate) else None

    def line_offer(
        lines: tuple[ReferencedText, ...],
        key: str,
        preference: int = 0,
        effects: tuple[FidelityEffect, ...] = (),
    ) -> RealizationOffer[RenderTargetT, ContextT, Realization]:
        fragments_items: list[ReferencedText] = []
        required: list[int] = []
        for index, item in enumerate(lines):
            length = sum(len(tree.plain(node)) for node in item.nodes)
            if pagination is not None and scope.text_capacity is not None and length > scope.text_capacity // 2:
                size = max(1, min(256, scope.text_capacity // 8))
                context.work.consume(1 + length // size)
                fragments_items.extend(
                    replace(item, nodes=nodes, continuation=index > 0)
                    for index, nodes in enumerate(text_fragments(item.nodes, size))
                )
            else:
                fragments_items.append(item)
            if pagination is not None and pagination.per and (index + 1) % pagination.per == 0:
                required.append(len(fragments_items))
        sequence = TextSequenceRealization(tuple(fragments_items), separator=line_separator)
        contract = None
        if pagination is not None:
            cursor_key = pagination.key or region_cursor_key(occurrence)
            contract = RegionContract(
                cursor_key,
                RegionNavigation(
                    occurrence, scope.policy.disabled_controls, pagination.footer, pagination.page, cursor_key
                ),
                min_fill=pagination.min_fill,
                widows=pagination.widows,
                owner=pagination.page,
                cursor_key=cursor_key,
                required_ends=tuple(required),
            )
        return RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            key,
            sequence if pagination is not None else line_payload(lines),
            fragments=FragmentContract(
                tuple(frozenset({item.reference}) for item in fragments_items),
                tuple(range(1, len(fragments_items) + 1)),
                sequence.fragment,
                continuations=pagination is not None,
            )
            if fragments_items
            else None,
            region=contract,
            preference_priority=line_priority,
            preference=preference,
            effects=effects,
        )

    full = line_offer(items, "lines")
    line_offers = [full]
    levels = [0] * len(items)
    if isinstance(overflow, Spill | Condense | Paginate):
        while eligible := [
            index
            for index, line in enumerate(value.lines)
            if isinstance(line, Alt) and levels[index] < len(line.fallbacks)
        ]:
            context.work.consume(len(items))
            largest = max(
                eligible, key=lambda index: (sum(len(tree.plain(node)) for node in items[index].nodes), -index)
            )
            line = value.lines[largest]
            assert isinstance(line, Alt)
            nodes = resolve_text(line.fallbacks[levels[largest]], context.localization).nodes
            levels[largest] += 1
            items = (*items[:largest], replace(items[largest], nodes=nodes), *items[largest + 1 :])
            replacement_effects = occurrence.scope_effects(
                tuple(Replacement(item.reference) for index, item in enumerate(items) if levels[index])
            )
            line_offers.append(
                line_offer(items, f"lines-alternate-{len(line_offers)}", len(line_offers), replacement_effects)
            )
        full = line_offers[-1]
        payload = line_payload(items)
    if isinstance(overflow, Paginate):
        return tuple(line_offers)
    if isinstance(overflow, Truncate | Condense):
        discovered = scope.text_offers(
            full, payload, overflow.keep if isinstance(overflow, Truncate) else "head", value.priority
        )
        if isinstance(discovered, OfferDiscovery):
            return replace(discovered, offers=(*line_offers[:-1], *discovered.offers))
        lengths = tuple(sum(len(tree.plain(node)) for node in item.nodes) for item in items)
        total = sum(lengths) + max(0, len(items) - 1) * len(value.join)
        shortened_lines: list[RealizationOffer[RenderTargetT, ContextT, Realization]] = list(line_offers[:-1])
        for offer in discovered:
            operation = offer.payload.shortening if isinstance(offer.payload, TextRealization) else None
            if operation is None:
                shortened_lines.append(offer)
                continue
            start, end = (0, operation.max_chars) if operation.keep == "head" else (total - operation.max_chars, total)
            offset = 0
            line_effects: list[Shortening] = []
            context.work.consume(len(items))
            for item, length in zip(items, lengths, strict=True):
                retained = max(0, min(end, offset + length) - max(start, offset))
                if retained < length:
                    line_effects.append(Shortening(item.reference, retained, operation.keep))
                offset += length + len(value.join)
            shortened_lines.append(
                replace(
                    offer,
                    preference=full.preference + offer.preference,
                    effects=(*full.effects, *occurrence.scope_effects(tuple(line_effects))),
                )
            )
        return tuple(shortened_lines)
    if isinstance(overflow, Never) or not items:
        return (full,)
    offers = list(line_offers)
    line_priorities = tuple(line.priority if isinstance(line, Alt) else 0 for line in value.lines)
    drop_order = sorted(
        range(len(items)),
        key=lambda index: (
            line_priorities[index],
            -index,
        ),
    )
    for removed in range(1, len(items) + 1) if isinstance(overflow, Spill) else (len(items),):
        dropped = set(drop_order[:removed])
        retained = tuple(item for index, item in enumerate(items) if index not in dropped)
        context.work.consume(len(items))
        effects = occurrence.scope_effects(
            tuple(
                Omission(item.reference) if index in dropped else Replacement(item.reference)
                for index, item in enumerate(items)
                if index in dropped or (isinstance(overflow, Spill) and levels[index])
            )
        )
        content = line_payload(retained).nodes
        if isinstance(overflow, Spill):
            content = (
                *content,
                *((tree.literal(value.join),) if content else ()),
                *resolve_text(context.chrome.and_n_more(removed), context.localization).nodes,
            )
        offers.append(
            replace(
                full,
                key=f"spill-{removed}",
                payload=TextRealization(content),
                effects=effects,
                preference=full.preference + removed,
                fragments=None,
            )
        )
    if isinstance(overflow, Spill):
        offers.append(
            replace(
                offers[-1],
                key="spill-empty",
                payload=TextRealization(()),
                preference=full.preference + len(items) + 1,
            )
        )
    return tuple(offers)


def _primitive_text_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT],
    value: primitive.Text | primitive.Heading | primitive.Footer | primitive.Code,
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    overflow = Truncate(scope.keep) if scope.keep is not None else value.overflow
    # Without entry ladders, condensation shortens prose and spilling drops it whole.
    if isinstance(overflow, Condense):
        overflow = Truncate()
    elif isinstance(overflow, Spill):
        overflow = Drop()
    if not scope.policy.native_controls or not isinstance(overflow, Never | Truncate | Alts | Drop | Paginate):
        return ()
    payload = TextRealization(resolve_text(value.content, context.localization).nodes)
    if isinstance(occurrence.graph.units[occurrence.reference].value, CardSlot):
        slot_plain = "".join(tree.plain(node) for node in payload.nodes)
        payload = replace(
            payload,
            nodes=_slice(payload.nodes, len(slot_plain) - len(slot_plain.lstrip()), len(slot_plain.rstrip())),
        )
    if isinstance(value, primitive.Heading):
        payload = replace(payload, role="heading", level=value.level)
    elif isinstance(value, primitive.Footer):
        if not any(tree.plain(node).strip() for node in payload.nodes):
            return (
                RealizationOffer[RenderTargetT, ContextT, Realization](
                    occurrence, "empty-note", FlowRealization(), flow=True
                ),
            )
        payload = replace(payload, role="note")
    elif isinstance(value, primitive.Code):
        payload = replace(payload, role="code", language=value.lang)
    if isinstance(overflow, Paginate):
        cursor_key = overflow.key or region_cursor_key(occurrence)
        nodes = payload.nodes
        if isinstance(value, primitive.Code):
            nodes = (tree.literal("".join(tree.plain(node) for node in nodes).replace("```", "``\u200b`")),)
        plain = "".join(tree.plain(node) for node in nodes)
        safe_ends = frozenset((0, *text_ends(nodes)))
        context.work.consume(len(plain) + len(safe_ends))
        spans: list[tuple[int, int]] = []
        start = offset = 0
        if overflow.boundary:
            while (offset := plain.find(overflow.boundary, offset)) >= 0:
                after = offset + len(overflow.boundary)
                if offset in safe_ends and after in safe_ends:
                    spans.append((start, offset))
                    start = after
                offset = after
        spans.append((start, len(plain)))
        parts: list[ReferencedText] = []
        ends: list[int] = []
        for index, (start, end) in enumerate(spans):
            chunk = _slice(nodes, start, end)
            pieces = (
                text_fragments(chunk, max(1, min(256, scope.text_capacity // 8)))
                if scope.text_capacity is not None and end - start > scope.text_capacity // 2
                else (chunk,)
            )
            parts.extend(
                ReferencedText(occurrence.reference, piece, continuation=i > 0) for i, piece in enumerate(pieces)
            )
            if overflow.per is not None and (index + 1) % overflow.per == 0:
                ends.append(len(parts))
        prefix = (
            f"```{value.lang}\n"
            if isinstance(value, primitive.Code)
            else "#" * value.level + " "
            if isinstance(value, primitive.Heading)
            else "-# "
            if isinstance(value, primitive.Footer)
            else ""
        )
        sequence = TextSequenceRealization(
            tuple(parts),
            overflow.boundary,
            prefix,
            "\n```" if isinstance(value, primitive.Code) else "",
            literal=isinstance(value, primitive.Code),
        )
        return (
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                "native-text-pages",
                sequence,
                fragments=FragmentContract(
                    tuple(frozenset({occurrence.reference}) for _ in parts),
                    tuple(range(1, len(parts) + 1)),
                    sequence.fragment,
                    continuations=True,
                ),
                region=RegionContract(
                    cursor_key,
                    RegionNavigation(
                        occurrence, scope.policy.disabled_controls, overflow.footer, overflow.page, cursor_key
                    ),
                    min_fill=overflow.min_fill,
                    widows=overflow.widows,
                    owner=overflow.page,
                    cursor_key=cursor_key,
                    required_ends=tuple(ends),
                ),
            ),
        )
    full = RealizationOffer[RenderTargetT, ContextT, Realization](
        occurrence, "native-text", payload, preference_priority=value.priority
    )
    if isinstance(overflow, Drop):
        return (
            full,
            replace(
                full,
                key="drop",
                payload=replace(payload, nodes=(), role="paragraph"),
                preference=1,
                effects=tuple(Omission(source) for source in sorted(occurrence.sources, key=lambda ref: ref.keys)),
            ),
        )
    if isinstance(overflow, Alts):
        effects = occurrence.scope_effects((Replacement(occurrence.reference),))
        replacements = tuple(
            replace(
                full,
                key=f"alternate-{index}",
                preference=index,
                payload=replace(payload, nodes=resolve_text(content, context.localization).nodes),
                effects=effects,
            )
            for index, content in enumerate(overflow.ladder, 1)
        )
        context.work.consume(len(replacements))
        last = replacements[-1]
        shortened = scope.text_offers(last, cast(TextRealization, last.payload), "head", value.priority)
        if isinstance(shortened, OfferDiscovery):
            return replace(shortened, offers=(full, *replacements[:-1], *shortened.offers))
        return (
            full,
            *replacements,
            *(replace(offer, preference=last.preference + offer.preference) for offer in shortened[1:]),
        )
    return scope.text_offers(
        full,
        payload,
        overflow.keep if isinstance(overflow, Truncate) else None,
        value.priority,
    )


def _gallery_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: primitive.Gallery | primitive.MediaCollection
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if not scope.policy.native_v2:
        return ()
    context.work.consume(len(value.items))
    gallery = GalleryRealization(
        tuple(
            scene.GalleryItem(
                item.url or (f"attachment://{item.asset_name}" if item.asset_name is not None else ""),
                resolve_text(item.description, context.localization).plain() if item.description is not None else None,
                item.spoiler,
                item.asset_name,
            )
            for raw in value.items
            for item in (primitive.normalize_gallery_item(raw),)
        ),
        collection=isinstance(value, primitive.MediaCollection),
    )
    refs = occurrence.graph.children.get(occurrence.reference, ())
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "native-gallery",
            gallery,
            fragments=FragmentContract(
                tuple(frozenset({ref}) for ref in refs), tuple(range(1, len(refs) + 1)), gallery.fragment
            )
            if gallery.collection and refs
            else None,
        ),
    )


def _file_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: primitive.File
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    return (
        (
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence,
                "native-file",
                FileRealization(scene.File(value.asset_key, value.name, value.media_type, value.spoiler)),
            ),
        )
        if scope.policy.native_v2
        else ()
    )


def _thumbnail_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: primitive.Thumbnail
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if not scope.policy.native_v2:
        return ()
    description = resolve_text(value.description, context.localization).plain() if value.description else None
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence, "thumbnail", ThumbnailRealization(scene.Thumbnail(value.url, description, value.spoiler))
        ),
    )


def _separator_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT], value: primitive.Sep
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    return (
        (
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence, "separator", SeparatorRealization(scene.Separator(value.large, value.visible))
            ),
        )
        if scope.policy.native_v2
        else ()
    )


def _primitive_container_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT],
    value: primitive.Row[ContextT]
    | primitive.ControlGroup[ContextT]
    | primitive.Section[ContextT]
    | primitive.Panel[ContextT]
    | primitive.Card
    | primitive.Content,
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if isinstance(value, primitive.Card | primitive.Content) and scope.policy.embed_limits is None:
        return ()
    if not scope.policy.native_controls or (
        isinstance(value, primitive.Section | primitive.Panel) and not scope.policy.native_v2
    ):
        return ()
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
        for ref in occurrence.graph.children.get(occurrence.reference, ())
    )
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "native-layout",
            CardRealization(value)
            if isinstance(value, primitive.Card)
            else ClassicContentRealization(
                sum(isinstance(unit.value, primitive.Content) for unit in occurrence.graph.units.values())
            )
            if isinstance(value, primitive.Content)
            else RowRealization()
            if isinstance(value, primitive.Row)
            else ActionsRealization()
            if isinstance(value, primitive.ControlGroup)
            else NativeSectionRealization()
            if isinstance(value, primitive.Section)
            else PanelRealization(value.accent, spoiler=value.spoiler),
            children,
            flow=isinstance(value, primitive.ControlGroup | primitive.Panel),
        ),
    )


def _primitive_control_offers[RenderTargetT, ContextT](
    scope: _OfferScope[RenderTargetT, ContextT],
    value: primitive.Button[ContextT]
    | primitive.RoutedButton
    | primitive.LinkButton
    | primitive.PremiumButton
    | primitive.SelectMenu[ContextT]
    | primitive.RoutedSelect
    | primitive.EntitySelect[ContextT],
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if not scope.policy.native_controls:
        return ()
    if isinstance(value, primitive.Button | primitive.SelectMenu | primitive.EntitySelect) and len(value.key) > 64:
        value = replace(value, key=_control_key(occurrence, context))
    candidate = navigation_candidate(value, occurrence, context)
    return (replace(candidate.offer, key="native-control", interaction=True),)

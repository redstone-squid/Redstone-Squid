"""Direct Components V2 rules for the shared composition boundary."""

from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from squid_i18n import tree
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.capabilities import Capability
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    CapacityExceeded,
    ConstraintViolation,
    Evaluation,
    LocalCapacityExceeded,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    ResourceIdentity,
    SemanticOccurrence,
    SharedResources,
    SharedUse,
    UnsupportedCapability,
)
from squid_ui.planning.composition_discord import discord_text, discord_time_prefix, select_violations
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.realizations import (
    ActionsRealization,
    CardRealization,
    ClassicContentRealization,
    ControlRealization,
    DownloadRealization,
    EntityRealization,
    FieldsRealization,
    FileRealization,
    FlowRealization,
    GalleryRealization,
    ImageRealization,
    NativeSectionRealization,
    PanelRealization,
    PremiumRealization,
    Realization,
    RowRealization,
    SelectRealization,
    SeparatorRealization,
    TableForm,
    TableRealization,
    TextRealization,
    TextSequenceRealization,
    ThumbnailRealization,
    TimeRealization,
    record_text,
    semantic_offers,
)
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentations import FidelityEffect, SemanticRef
from squid_ui.primitives import nodes as primitive
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import Markup


@dataclass(frozen=True, slots=True)
class V2CompositionRules[RenderTargetT, ContextT]:
    limits: V2Limits = V2Limits()

    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
        | OfferDiscovery[RenderTargetT, ContextT, Realization]
    ):
        value = occurrence.graph.units[occurrence.reference].value
        if isinstance(value, primitive.Section) and not 1 <= len(value.texts) <= self.limits.section_texts:
            context.work.consume()
            return OfferDiscovery(())
        return semantic_offers(
            occurrence,
            context,
            table_forms=(TableForm.RECORDS,),
            panels=True,
            nested_panels=False,
            character_budgets=True,
            native_controls=True,
            native_v2=True,
            grid_columns=self.limits.components.row_buttons,
        )

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.ComponentsV2]:
        texts: list[str] = []
        text_chars = 0
        scopes: dict[OccurrenceId, ResourceCost] = {}
        scoped_resources: dict[OccurrenceId, frozenset[ResourceIdentity]] = {}
        uses: list[SharedUse[Asset]] = [
            SharedUse(ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1}))
            for asset in context.assets
        ]
        violations: list[CandidateViolation] = []
        effects: list[FidelityEffect] = []
        interactions: set[SemanticRef] = set()
        components = 0

        def text(nodes: tuple[tree.Node, ...]) -> tuple[scene.Node, ...]:
            nonlocal components, text_chars
            return rendered_text(tree.markdown(nodes, discord=True, escape=context.escape_text))

        def rendered_text(rendered: str) -> tuple[scene.Node, ...]:
            nonlocal components, text_chars
            text_chars += len(rendered)
            if rendered:
                components += 1
                texts.append(rendered)
                return (scene.Text(rendered, Markup.DISCORD_MARKDOWN),)
            return ()

        def realize(
            part: CandidateComposition[RenderTargetT, ContextT, Realization], *, in_panel: bool, in_row: bool = False
        ) -> tuple[scene.Node, ...]:
            nonlocal components
            context.work.consume()
            effects.extend(part.offer.effects)
            if part.offer.interaction:
                interactions.update(part.offer.occurrence.sources)
            match part.offer.payload:
                case NativeSectionRealization():
                    identity = part.offer.occurrence.identity
                    components += 1
                    children = tuple(node for child in part.children[:-1] for node in visit(child, in_panel=in_panel))
                    accessories = visit(part.children[-1], in_panel=in_panel, in_row=True)
                    if (
                        not 1 <= len(children) <= self.limits.section_texts
                        or len(part.children) - 1 > self.limits.section_texts
                        or any(not isinstance(node, scene.Text) for node in children)
                    ):
                        violations.append(ConstraintViolation(identity, "section text slots exceed native limits"))
                    if len(accessories) != 1 or not isinstance(
                        accessories[0],
                        scene.Thumbnail | scene.Button | scene.RoutedButton | scene.Link | scene.PremiumButton,
                    ):
                        violations.append(ConstraintViolation(identity, "section needs one accessory"))
                        return ()
                    return (
                        scene.Section(tuple(node for node in children if isinstance(node, scene.Text)), accessories[0]),
                    )
                case ThumbnailRealization(node=node):
                    components += 1
                    if not in_row:
                        violations.append(
                            ConstraintViolation(part.offer.occurrence.identity, "thumbnail needs a section")
                        )
                    if len(node.description or "") > self.limits.gallery_item_description:
                        violations.append(
                            LocalCapacityExceeded(
                                part.offer.occurrence.identity,
                                "thumbnail.description",
                                len(node.description or ""),
                                self.limits.gallery_item_description,
                            )
                        )
                    return (node,)
                case SeparatorRealization(node=node):
                    components += 1
                    return (node,)
                case PremiumRealization(sku_id=sku_id):
                    identity = part.offer.occurrence.identity
                    if Capability.ACTIONS_DISCORD_PREMIUM not in context.capabilities:
                        violations.append(UnsupportedCapability(identity, "Discord premium buttons"))
                    if self.limits.components.row_buttons < 1:
                        violations.append(LocalCapacityExceeded(identity, "row.buttons", 1, 0))
                    components += 1 if in_row else 2
                    premium = scene.PremiumButton(sku_id)
                    return (premium,) if in_row else (scene.Row((premium,)),)
                case ControlRealization() as control:
                    identity = part.offer.occurrence.identity
                    for name, used, capacity in (
                        ("button.label", len(control.label), self.limits.components.button_label),
                        ("button.url", len(control.url or ""), self.limits.components.link_url),
                        ("button.id", len(control.action or control.route or ""), self.limits.components.custom_id),
                        ("row.buttons", 1, self.limits.components.row_buttons),
                    ):
                        if used > capacity:
                            violations.append(LocalCapacityExceeded(identity, name, used, capacity))
                    if not control.label and control.emoji is None:
                        violations.append(ConstraintViolation(identity, "button needs a label"))
                    components += 1 if in_row else 2
                    if control.action is not None:
                        button = scene.Button(
                            control.label,
                            control.action,
                            control.style,
                            emoji=control.emoji,
                            disabled=control.disabled,
                            mode=control.mode,
                        )
                    elif control.route is not None:
                        button = scene.RoutedButton(
                            control.label, control.route, control.style, emoji=control.emoji, disabled=control.disabled
                        )
                    else:
                        button = scene.Link(
                            control.label, control.url or "", emoji=control.emoji, disabled=control.disabled
                        )
                    return (button,) if in_row else (scene.Row((button,)),)
                case ActionsRealization(columns=columns):
                    output: list[scene.Node] = []
                    pending: list[scene.Button | scene.RoutedButton | scene.Link | scene.PremiumButton] = []
                    size = max(1, columns if columns is not None else self.limits.components.row_buttons)
                    if columns is not None and columns > self.limits.components.row_buttons:
                        violations.append(
                            LocalCapacityExceeded(
                                part.offer.occurrence.identity,
                                "grid.columns",
                                columns,
                                self.limits.components.row_buttons,
                            )
                        )

                    def flush() -> None:
                        nonlocal components
                        if pending:
                            output.append(scene.Row(tuple(pending)))
                            pending.clear()
                            components += 1

                    for child in part.children:
                        if isinstance(child.offer.payload, ActionsRealization):
                            flush()
                        for item in visit(child, in_panel=in_panel, in_row=True):
                            if isinstance(item, scene.Button | scene.RoutedButton | scene.Link | scene.PremiumButton):
                                pending.append(item)
                                if len(pending) == size:
                                    flush()
                            else:
                                flush()
                                output.append(item)
                    flush()
                    return tuple(output)
                case RowRealization():
                    components += 1
                    if not part.children:
                        violations.append(ConstraintViolation(part.offer.occurrence.identity, "row needs a button"))
                    if len(part.children) > self.limits.components.row_buttons:
                        violations.append(
                            LocalCapacityExceeded(
                                part.offer.occurrence.identity,
                                "row.buttons",
                                len(part.children),
                                self.limits.components.row_buttons,
                            )
                        )
                    items: list[scene.Button | scene.RoutedButton | scene.Link | scene.PremiumButton] = []
                    for child in part.children:
                        for item in visit(child, in_panel=in_panel, in_row=True):
                            if not isinstance(
                                item, scene.Button | scene.RoutedButton | scene.Link | scene.PremiumButton
                            ):
                                message = "navigation row contains a non-button"
                                raise DrawInvariantError(message)
                            items.append(item)
                    return (scene.Row(tuple(items)),)
                case EntityRealization(node=node):
                    if Capability.ACTIONS_ENTITY not in context.capabilities:
                        violations.append(
                            UnsupportedCapability(part.offer.occurrence.identity, "native entity discovery")
                        )
                    violations.extend(
                        ConstraintViolation(part.offer.occurrence.identity, detail)
                        for detail in select_violations(node, self.limits.components)
                    )
                    components += 2
                    return (node,)
                case SelectRealization(node=node, summaries=summaries):
                    overview = (
                        rendered_text(
                            "\n".join(
                                tree.markdown(item.nodes, discord=True, escape=context.escape_text)
                                for item in summaries
                            )
                        )
                        if summaries is not None
                        else ()
                    )
                    components += 2
                    violations.extend(
                        ConstraintViolation(part.offer.occurrence.identity, detail)
                        for detail in select_violations(node, self.limits.components)
                    )
                    return (
                        *overview,
                        node,
                        *(node for child in part.children for node in visit(child, in_panel=in_panel)),
                    )
                case TextSequenceRealization() as sequence:
                    rendered = (
                        sequence.prefix
                        + "".join(
                            (sequence.separator if index and not item.continuation else "")
                            + item.prefix
                            + (
                                "".join(tree.plain(node) for node in item.nodes)
                                if sequence.literal
                                else tree.markdown(item.nodes, discord=True, escape=context.escape_text)
                            )
                            for index, item in enumerate(sequence.display_items)
                        )
                        + sequence.suffix
                    )
                    if sequence.notice:
                        rendered += ("\n" if rendered else "") + tree.markdown(
                            sequence.notice, discord=True, escape=context.escape_text
                        )
                    return (
                        *rendered_text(rendered),
                        *(node for child in part.children for node in visit(child, in_panel=in_panel)),
                    )
                case TimeRealization() as time:
                    rendered_text(discord_text(time, context.escape_text))
                    prefix = discord_time_prefix(time, context.escape_text) or None
                    return (
                        scene.Time(time.instant, time.style or "f", prefix)
                        if time.timezone is None
                        else scene.ZonedTime(time.instant, time.timezone, prefix),
                    )
                case TextRealization() as payload:
                    return rendered_text(discord_text(payload, context.escape_text))
                case TableRealization() as table:
                    context.work.consume(len(table.columns) + sum(1 + len(row.cells) for row in table.rows))
                    if table.form != TableForm.RECORDS:
                        violations.append(UnsupportedCapability(part.offer.occurrence.identity, "native table"))
                    return tuple(node for row in record_text(table) for node in text(row))
                case FileRealization(node=node):
                    identity = part.offer.occurrence.identity
                    matches = [asset for asset in context.assets if asset.key == node.asset_key]
                    if len(matches) != 1 or (node.name, node.media_type) != (matches[0].name, matches[0].media_type):
                        violations.append(ConstraintViolation(identity, "file must match one declared asset"))
                    else:
                        uses.append(
                            SharedUse(
                                ResourceIdentity("attachment", node.asset_key),
                                matches[0],
                                ResourceCost({Axis.ATTACHMENTS: 1}),
                            )
                        )
                    components += 1
                    return (node,)
                case GalleryRealization(items=items, collection=collection):
                    identity = part.offer.occurrence.identity
                    if (not items and not collection) or self.limits.gallery_items < 1:
                        violations.append(ConstraintViolation(identity, "gallery needs an item and positive capacity"))
                    if not collection and len(items) > self.limits.gallery_items:
                        violations.append(
                            LocalCapacityExceeded(identity, "gallery.items", len(items), self.limits.gallery_items)
                        )
                    for item in items:
                        context.work.consume()
                        if len(item.description or "") > self.limits.gallery_item_description:
                            violations.append(
                                LocalCapacityExceeded(
                                    identity,
                                    "gallery.description",
                                    len(item.description or ""),
                                    self.limits.gallery_item_description,
                                )
                            )
                        if item.asset_name is not None:
                            matches = [asset for asset in context.assets if asset.name == item.asset_name]
                            if len(matches) != 1 or item.url != f"attachment://{item.asset_name}":
                                violations.append(
                                    ConstraintViolation(identity, "gallery image must match one declared attachment")
                                )
                            else:
                                uses.append(
                                    SharedUse(
                                        ResourceIdentity("attachment", matches[0].key),
                                        matches[0],
                                        ResourceCost({Axis.ATTACHMENTS: 1}),
                                    )
                                )
                        elif item.url.startswith("attachment://"):
                            violations.append(ConstraintViolation(identity, "gallery attachment needs its asset name"))
                    size = max(1, self.limits.gallery_items) if collection else max(1, len(items))
                    galleries = tuple(
                        scene.Gallery(items[start : start + size]) for start in range(0, len(items), size)
                    )
                    components += len(galleries)
                    return galleries
                case ImageRealization() as media:
                    components += 1
                    if len(media.description) > self.limits.gallery_item_description:
                        violations.append(
                            LocalCapacityExceeded(
                                part.offer.occurrence.identity,
                                "image.description",
                                len(media.description),
                                self.limits.gallery_item_description,
                            )
                        )
                    if self.limits.gallery_items < 1:
                        violations.append(
                            LocalCapacityExceeded(
                                part.offer.occurrence.identity, "gallery.items", 1, self.limits.gallery_items
                            )
                        )
                    asset = media.source if isinstance(media.source, Asset) else None
                    if asset is not None:
                        uses.append(
                            SharedUse(
                                ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1})
                            )
                        )
                    url = f"attachment://{asset.name}" if asset is not None else str(media.source)
                    return (
                        scene.Gallery(
                            (
                                scene.GalleryItem(
                                    url,
                                    media.description or None,
                                    media.spoiler,
                                    asset.name if asset is not None else None,
                                ),
                            )
                        ),
                    )
                case DownloadRealization() as download:
                    components += 1
                    uses.append(
                        SharedUse(
                            ResourceIdentity("attachment", download.asset.key),
                            download.asset,
                            ResourceCost({Axis.ATTACHMENTS: 1}),
                        )
                    )
                    return (
                        *text(download.label),
                        *text(download.description),
                        scene.File(
                            download.asset.key, download.asset.name, download.asset.media_type, download.spoiler
                        ),
                    )
                case PanelRealization() as panel:
                    components += int(panel.framed)
                    if in_panel and panel.framed:
                        violations.append(ConstraintViolation(part.offer.occurrence.identity, "nested V2 container"))
                    children = tuple(
                        node for child in part.children for node in visit(child, in_panel=in_panel or panel.framed)
                    )
                    if panel.thumbnail is not None:
                        if not children or not isinstance(children[0], scene.Text):
                            violations.append(
                                ConstraintViolation(part.offer.occurrence.identity, "thumbnail needs a heading")
                            )
                        else:
                            components += 2
                            if self.limits.section_texts < 1:
                                violations.append(
                                    LocalCapacityExceeded(
                                        part.offer.occurrence.identity, "section.texts", 1, self.limits.section_texts
                                    )
                                )
                            children = (scene.Section((children[0],), scene.Thumbnail(panel.thumbnail)), *children[1:])
                    return (scene.Panel(children, panel.accent, panel.spoiler),) if panel.framed else children
                case FieldsRealization(notice=notice):
                    return (
                        *(node for child in part.children for node in visit(child, in_panel=in_panel)),
                        *text(notice),
                    )
                case FlowRealization():
                    return tuple(node for child in part.children for node in visit(child, in_panel=in_panel))
                case CardRealization() | ClassicContentRealization():
                    violations.append(
                        UnsupportedCapability(part.offer.occurrence.identity, "classic message structure")
                    )
                    return ()
                case unknown:
                    assert_never(unknown)

        def visit(
            part: CandidateComposition[RenderTargetT, ContextT, Realization], *, in_panel: bool, in_row: bool = False
        ) -> tuple[scene.Node, ...]:
            components_before, text_before, uses_before = components, text_chars, len(uses)
            nodes = realize(part, in_panel=in_panel, in_row=in_row)
            # An asset used by a sibling still counts within this subtree's budget.
            shared = SharedResources.combine(uses[uses_before:])
            scoped_resources[part.offer.occurrence.identity] = frozenset(shared.uses)
            scopes[part.offer.occurrence.identity] = (
                ResourceCost(
                    {Axis.COMPONENTS: components - components_before, Axis.DISPLAY_TEXT: text_chars - text_before}
                )
                + shared.cost
            )
            return nodes

        preparation = scene.ComponentsV2(visit(candidate, in_panel=False))
        resources = SharedResources.combine(uses)
        additive = ResourceCost({Axis.COMPONENTS: components, Axis.DISPLAY_TEXT: sum(map(len, texts))})
        violations.extend(
            CapacityExceeded(candidate.offer.occurrence.identity, axis, used, capacity)
            for axis, used, capacity in (additive + resources.cost).over(self.limits.capacities)
        )
        return Evaluation(
            additive,
            resources,
            tuple(violations),
            tuple(effects),
            interactions=frozenset(interactions),
            preparation=preparation,
            text=tuple(texts),
            resource_axes=frozenset({Axis.COMPONENTS, Axis.DISPLAY_TEXT, Axis.ATTACHMENTS}),
            scoped_costs=scopes,
            scoped_resources=scoped_resources,
        )

    def build(self, preparation: scene.ComponentsV2, context: PlanningContext) -> scene.ComponentsV2:
        context.work.consume()
        return preparation

    def validate(
        self, body: scene.ComponentsV2, expected: Evaluation[Asset, scene.ComponentsV2], context: PlanningContext
    ) -> None:
        texts: list[str] = []
        assets: set[str] = {asset.key for asset in context.assets}
        components = 0

        def visit(node: scene.Node, *, in_panel: bool) -> None:
            nonlocal components
            context.work.consume()
            components += 1
            match node:
                case scene.Row(items=items):
                    if len(items) > self.limits.components.row_buttons:
                        msg = "built V2 action row exceeds its limit"
                        raise DrawInvariantError(msg)
                    for item in items:
                        visit(item, in_panel=in_panel)
                case scene.Button() | scene.RoutedButton() | scene.Link():
                    if (not node.label and node.emoji is None) or len(
                        node.label or ""
                    ) > self.limits.components.button_label:
                        msg = "built V2 button violates its label limit"
                        raise DrawInvariantError(msg)
                    identifier = (
                        node.action
                        if isinstance(node, scene.Button)
                        else node.route_id
                        if isinstance(node, scene.RoutedButton)
                        else ""
                    )
                    if len(identifier) > self.limits.components.custom_id or (
                        isinstance(node, scene.Link) and len(node.url) > self.limits.components.link_url
                    ):
                        msg = "built V2 button violates its destination limit"
                        raise DrawInvariantError(msg)
                case scene.Select() | scene.RoutedSelect() | scene.EntitySelect():
                    components += 1  # Selects occupy an implicit action row.
                    if select_violations(node, self.limits.components):
                        msg_0 = "built V2 select violates its limits"
                        raise DrawInvariantError(msg_0)
                case scene.Text(content=content):
                    texts.append(content)
                case scene.Time():
                    instant = datetime.fromisoformat(node.instant)
                    texts.append((node.prefix or "") + f"<t:{int(instant.timestamp())}:{node.style}>")
                case scene.ZonedTime():
                    zoned = ZonedDateTime(datetime.fromisoformat(node.instant), node.timezone)
                    texts.append((node.prefix or "") + zoned.isoformat())
                case scene.Gallery(items=items):
                    if len(items) > self.limits.gallery_items or any(
                        len(item.description or "") > self.limits.gallery_item_description for item in items
                    ):
                        message = "built gallery exceeds its limits"
                        raise DrawInvariantError(message)
                    for item in items:
                        if item.asset_name is not None:
                            matches = [
                                use.value.key
                                for use in expected.resources.uses.values()
                                if use.value.name == item.asset_name
                            ]
                            if not matches:
                                message = "built gallery has an undeclared asset"
                                raise DrawInvariantError(message)
                            assets.update(matches)
                case scene.File():
                    assets.add(node.asset_key)
                    use = expected.resources.uses.get(ResourceIdentity("attachment", node.asset_key))
                    if use is None or (node.name, node.media_type) != (use.value.name, use.value.media_type):
                        message = "built file disagrees with its declared shared asset"
                        raise DrawInvariantError(message)
                case scene.Section(texts=children, accessory=accessory):
                    if not 1 <= len(children) <= self.limits.section_texts or not isinstance(
                        accessory,
                        scene.Thumbnail | scene.Button | scene.RoutedButton | scene.Link | scene.PremiumButton,
                    ):
                        message = "built thumbnail section violates its structure"
                        raise DrawInvariantError(message)
                    for child in children:
                        visit(child, in_panel=in_panel)
                    visit(accessory, in_panel=in_panel)
                case scene.Thumbnail():
                    if len(node.description or "") > self.limits.gallery_item_description:
                        message = "built thumbnail description exceeds its limit"
                        raise DrawInvariantError(message)
                case scene.PremiumButton():
                    if node.sku_id <= 0 or Capability.ACTIONS_DISCORD_PREMIUM not in context.capabilities:
                        message = "built premium button lacks a valid SKU or capability"
                        raise DrawInvariantError(message)
                case scene.Separator():
                    pass
                case scene.Panel():
                    if in_panel:
                        message = "built V2 container is nested inside another container"
                        raise DrawInvariantError(message)
                    for child in node.children:
                        visit(child, in_panel=True)
                case _:
                    message = "unexpected node in shared V2 composition"
                    raise DrawInvariantError(message)

        for node in body.children:
            visit(node, in_panel=False)
        actual = ResourceCost(
            {Axis.COMPONENTS: components, Axis.DISPLAY_TEXT: sum(map(len, texts)), Axis.ATTACHMENTS: len(assets)}
        )
        if (
            actual != expected.cost
            or tuple(texts) != expected.text
            or body != expected.preparation
            or not actual.within(self.limits.capacities)
        ):
            message = "built V2 composition disagrees with evaluation"
            raise DrawInvariantError(message)

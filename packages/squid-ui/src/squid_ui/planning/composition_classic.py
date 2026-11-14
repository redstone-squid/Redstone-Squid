"""Classic Discord realizes shared candidates into separate text and control pools."""

from dataclasses import dataclass, replace

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
from squid_ui.planning.composition_discord import discord_text, select_violations
from squid_ui.planning.limits import ClassicLimits
from squid_ui.planning.realizations import (
    ActionsRealization,
    ControlRealization,
    DownloadRealization,
    EntityRealization,
    FieldRealization,
    FieldsRealization,
    FlowRealization,
    ImageRealization,
    PanelRealization,
    PremiumRealization,
    Realization,
    RowRealization,
    SelectRealization,
    TableForm,
    TableRealization,
    TextRealization,
    TextSequenceRealization,
    record_text,
    semantic_offers,
)
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentations import FidelityEffect, SemanticRef


@dataclass(frozen=True, slots=True)
class ClassicCompositionRules[RenderTargetT, ContextT]:
    limits: ClassicLimits = ClassicLimits()

    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
        | OfferDiscovery[RenderTargetT, ContextT, Realization]
    ):
        return semantic_offers(
            occurrence,
            context,
            table_forms=(TableForm.RECORDS,),
            panels=True,
            nested_panels=False,
            text_limit=min(self.limits.content, self.limits.embeds.description),
            native_controls=True,
            panel_text_limit=self.limits.embeds.description,
            grid_columns=self.limits.components.row_buttons,
        )

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.ClassicMessage]:
        content: list[str] = []
        texts: list[str] = []
        embeds: list[scene.Embed] = []
        rows: list[scene.ClassicRow] = []
        uses = [
            SharedUse(ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1}))
            for asset in context.assets
        ]
        violations: list[CandidateViolation] = []
        effects: list[FidelityEffect] = []
        interactions: set[SemanticRef] = set()
        scopes: dict[OccurrenceId, ResourceCost] = {}
        scoped_resources: dict[OccurrenceId, frozenset[ResourceIdentity]] = {}
        field_stack: list[list[scene.EmbedField]] = [[]]

        def field_text(fields: list[scene.EmbedField] | tuple[scene.EmbedField, ...]) -> int:
            return sum(len(field.name) + len(field.value) for field in fields)

        def cards(fields: list[scene.EmbedField], base: scene.Embed) -> None:
            size = max(1, self.limits.embeds.fields)
            if fields and self.limits.embeds.fields == 0:
                violations.append(ConstraintViolation(candidate.offer.occurrence.identity, "embed fields unavailable"))
            embeds.extend(
                replace(
                    base if start == 0 else scene.Embed(colour=base.colour), fields=tuple(fields[start : start + size])
                )
                for start in range(0, max(1, len(fields)), size)
            )

        def visit(
            part: CandidateComposition[RenderTargetT, ContextT, Realization],
            output: list[str],
            *,
            embedded: bool = False,
            row: list[scene.Control] | None = None,
        ) -> None:
            context.work.consume()
            identity = part.offer.occurrence.identity
            start, embed_start, row_start, use_start = len(output), len(embeds), len(rows), len(uses)
            control_start = len(row) if row is not None else 0
            fields, field_start = field_stack[-1], len(field_stack[-1])
            effects.extend(part.offer.effects)
            if part.offer.interaction:
                interactions.update(part.offer.occurrence.sources)

            def text(value: str) -> None:
                if value:
                    output.append(value)
                    texts.append(value)

            def markdown(nodes: tuple[tree.Node, ...]) -> str:
                return tree.markdown(nodes, discord=True, escape=context.escape_text)

            def limit(name: str, used: int, capacity: int) -> None:
                if used > capacity:
                    violations.append(LocalCapacityExceeded(identity, name, used, capacity))

            match part.offer.payload:
                case FieldRealization(label=label, value=value):
                    name, content = markdown(label), markdown(value)
                    limit("embed.field_name", len(name), self.limits.embeds.field_name)
                    limit("embed.field_value", len(content), self.limits.embeds.field_value)
                    if not name or not content:
                        violations.append(ConstraintViolation(identity, "embed field needs a name and value"))
                    entry = scene.EmbedField(name, content)
                    if len(field_stack) == 1:
                        cards([entry], scene.Embed())
                    else:
                        fields.append(entry)
                    texts.extend((name, content))
                case FieldsRealization(notice=notice):
                    entries: list[scene.EmbedField] = []
                    field_stack.append(entries)
                    for child in part.children:
                        visit(child, output, embedded=True)
                    field_stack.pop()
                    if embedded:
                        fields.extend(entries)
                    elif entries:
                        cards(entries, scene.Embed())
                    text(markdown(notice))
                case TextRealization() as payload:
                    text(discord_text(payload, context.escape_text))
                case TextSequenceRealization() as sequence:
                    text(
                        sequence.prefix
                        + "".join(
                            (sequence.separator if index and not item.continuation else "")
                            + item.prefix
                            + (
                                "".join(tree.plain(node) for node in item.nodes)
                                if sequence.literal
                                else markdown(item.nodes)
                            )
                            for index, item in enumerate(sequence.display_items)
                        )
                        + sequence.suffix
                        + (
                            ("\n" if sequence.display_items else "") + markdown(sequence.notice)
                            if sequence.notice
                            else ""
                        )
                    )
                    for child in part.children:
                        visit(child, output, embedded=embedded)
                case TableRealization() as table:
                    for nodes in record_text(table):
                        text(markdown(nodes))
                case PanelRealization() as panel:
                    if not panel.framed:
                        if panel.thumbnail is not None:
                            violations.append(
                                UnsupportedCapability(identity, "thumbnail inside an enclosing classic embed")
                            )
                        for child in part.children:
                            visit(child, output, embedded=embedded)
                    else:
                        if embedded:
                            violations.append(UnsupportedCapability(identity, "nested classic embeds"))
                        description: list[str] = []
                        entries = []
                        field_stack.append(entries)
                        for child in part.children:
                            visit(child, description, embedded=True)
                        field_stack.pop()
                        value = "\n".join(description)
                        limit("embed.description", len(value), self.limits.embeds.description)
                        cards(
                            entries,
                            scene.Embed(
                                description=value or None,
                                colour=panel.accent,
                                thumbnail=scene.EmbedMedia(panel.thumbnail) if panel.thumbnail is not None else None,
                            ),
                        )

                case PremiumRealization(sku_id=sku_id):
                    if Capability.ACTIONS_DISCORD_PREMIUM not in context.capabilities:
                        violations.append(UnsupportedCapability(identity, "Discord premium buttons"))
                    limit("row.buttons", 1, self.limits.components.row_buttons)
                    premium = scene.PremiumButton(sku_id)
                    if row is None:
                        rows.append(scene.ClassicRow((premium,)))
                    else:
                        row.append(premium)
                case ControlRealization() as control:
                    limit("row.buttons", 1, self.limits.components.row_buttons)
                    limit("button.label", len(control.label), self.limits.components.button_label)
                    limit("button.url", len(control.url or ""), self.limits.components.link_url)
                    limit("button.id", len(control.action or control.route or ""), self.limits.components.custom_id)
                    if not control.label and control.emoji is None:
                        violations.append(ConstraintViolation(identity, "button needs a label"))
                    button = (
                        scene.Button(
                            control.label,
                            control.action,
                            control.style,
                            emoji=control.emoji,
                            disabled=control.disabled,
                            mode=control.mode,
                        )
                        if control.action is not None
                        else scene.RoutedButton(
                            control.label, control.route, control.style, emoji=control.emoji, disabled=control.disabled
                        )
                        if control.route is not None
                        else scene.Link(
                            control.label, control.url or "", emoji=control.emoji, disabled=control.disabled
                        )
                    )
                    if row is None:
                        rows.append(scene.ClassicRow((button,)))
                    else:
                        row.append(button)
                case EntityRealization(node=node):
                    if Capability.ACTIONS_ENTITY not in context.capabilities:
                        violations.append(
                            UnsupportedCapability(part.offer.occurrence.identity, "native entity discovery")
                        )
                    violations.extend(
                        ConstraintViolation(part.offer.occurrence.identity, detail)
                        for detail in select_violations(node, self.limits.components)
                    )
                    rows.append(scene.ClassicRow((node,)))
                case SelectRealization(node=node, summaries=summaries):
                    if summaries is not None:
                        text("\n".join(markdown(item.nodes) for item in summaries))
                    violations.extend(
                        ConstraintViolation(identity, detail)
                        for detail in select_violations(node, self.limits.components)
                    )
                    rows.append(scene.ClassicRow((node,)))
                    for child in part.children:
                        visit(child, output, embedded=embedded)
                case ActionsRealization(columns=columns):
                    controls: list[scene.Control] = []
                    size = max(1, columns if columns is not None else self.limits.components.row_buttons)
                    if columns is not None:
                        limit("grid.columns", columns, self.limits.components.row_buttons)

                    def flush() -> None:
                        if controls:
                            rows.append(scene.ClassicRow(tuple(controls)))
                            controls.clear()

                    for child in part.children:
                        if isinstance(child.offer.payload, ControlRealization | PremiumRealization):
                            visit(child, output, embedded=embedded, row=controls)
                            if len(controls) == size:
                                flush()
                        else:
                            flush()
                            visit(child, output, embedded=embedded)
                    flush()
                case RowRealization():
                    controls = []
                    for child in part.children:
                        visit(child, output, embedded=embedded, row=controls)
                    limit("row.buttons", len(controls), self.limits.components.row_buttons)
                    if not controls:
                        violations.append(ConstraintViolation(identity, "row needs a button"))
                    if controls:
                        rows.append(scene.ClassicRow(tuple(controls)))
                case ImageRealization() as media:
                    if media.spoiler:
                        violations.append(UnsupportedCapability(identity, "classic embed spoiler image"))
                    asset = media.source if isinstance(media.source, Asset) else None
                    if asset is not None:
                        uses.append(
                            SharedUse(
                                ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1})
                            )
                        )
                    url = f"attachment://{asset.name}" if asset is not None else str(media.source)
                    limit("embed.description", len(media.description), self.limits.embeds.description)
                    if media.description:
                        texts.append(media.description)
                    embeds.append(
                        scene.Embed(
                            description=media.description or None,
                            image=scene.EmbedMedia(
                                url, media.description or None, asset.name if asset is not None else None
                            ),
                        )
                    )
                case DownloadRealization():
                    # Classic attachments have no inline file widget or spoiler metadata.
                    violations.append(UnsupportedCapability(identity, "inline classic download; use document assets"))
                case FlowRealization():
                    for child in part.children:
                        visit(child, output, embedded=embedded, row=row)
                case unknown:
                    violations.append(UnsupportedCapability(identity, type(unknown).__name__))
            shared = SharedResources.combine(uses[use_start:])
            scoped_resources[identity] = frozenset(shared.uses)
            scopes[identity] = (
                ResourceCost(
                    {
                        Axis.CONTENT_TEXT: 0 if embedded else len("\n".join(output[start:])),
                        Axis.EMBED_TEXT: (len("\n".join(output[start:])) if embedded else 0)
                        + sum(len(item.description or "") + field_text(item.fields) for item in embeds[embed_start:])
                        + field_text(fields[field_start:]),
                        Axis.EMBEDS: len(embeds) - embed_start,
                        Axis.ROWS: len(rows) - row_start,
                        Axis.CONTROLS: sum(len(item.controls) for item in rows[row_start:])
                        + (len(row) - control_start if row is not None else 0),
                    }
                )
                + shared.cost
            )

        visit(candidate, content)
        body = scene.ClassicMessage("\n".join(content) or None, tuple(embeds), tuple(rows))
        resources = SharedResources.combine(uses)
        cost = self._cost(body)
        violations.extend(
            CapacityExceeded(candidate.offer.occurrence.identity, axis, used, capacity)
            for axis, used, capacity in (cost + resources.cost).over(self.limits.capacities)
        )
        return Evaluation(
            cost,
            resources,
            tuple(violations),
            tuple(effects),
            interactions=frozenset(interactions),
            preparation=body,
            text=tuple(texts),
            resource_axes=frozenset(self.limits.capacities),
            scoped_costs=scopes,
            scoped_resources=scoped_resources,
        )

    def _cost(self, body: scene.ClassicMessage) -> ResourceCost:
        return ResourceCost(
            {
                Axis.CONTENT_TEXT: len(body.content or ""),
                Axis.EMBED_TEXT: sum(
                    len(embed.description or "") + sum(len(field.name) + len(field.value) for field in embed.fields)
                    for embed in body.embeds
                ),
                Axis.EMBEDS: len(body.embeds),
                Axis.ROWS: len(body.rows),
                Axis.CONTROLS: sum(len(row.controls) for row in body.rows),
            }
        )

    def build(self, preparation: scene.ClassicMessage, context: PlanningContext) -> scene.ClassicMessage:
        context.work.consume()
        return preparation

    def validate(
        self, body: scene.ClassicMessage, expected: Evaluation[Asset, scene.ClassicMessage], context: PlanningContext
    ) -> None:
        context.work.consume(1 + len(body.embeds) + sum(1 + len(row.controls) for row in body.rows))
        actual = self._cost(body) + expected.resources.cost
        if (
            body != expected.preparation
            or actual != expected.cost
            or not actual.within(self.limits.capacities)
            or any(len(embed.description or "") > self.limits.embeds.description for embed in body.embeds)
            or any(len(embed.fields) > self.limits.embeds.fields for embed in body.embeds)
            or any(
                not field.name
                or not field.value
                or len(field.name) > self.limits.embeds.field_name
                or len(field.value) > self.limits.embeds.field_value
                for embed in body.embeds
                for field in embed.fields
            )
            or any(len(row.controls) > self.limits.components.row_buttons for row in body.rows)
        ):
            message = "built classic composition disagrees with evaluation"
            raise DrawInvariantError(message)

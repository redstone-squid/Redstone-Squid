"""Classic Discord realizes shared candidates into separate text and control pools."""

from dataclasses import asdict, dataclass

from squid_i18n import tree
from squid_ui import scene
from squid_ui.authoring.presentations import FidelityEffect, SemanticRef
from squid_ui.capabilities import Capability
from squid_ui.content.assets import Asset
from squid_ui.content.temporal import ZonedDateTime
from squid_ui.content.text import resolve_text
from squid_ui.errors import DrawInvariantError
from squid_ui.ext.discord.limits import ClassicLimits
from squid_ui.ext.discord.planning.composition_discord import discord_text, select_violations
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    CapacityExceeded,
    ConstraintViolation,
    Evaluation,
    LocalCapacityExceeded,
    OccurrenceId,
    OfferDiscovery,
    Placement,
    PlanningContext,
    RealizationOffer,
    ResourceIdentity,
    SemanticOccurrence,
    SharedResources,
    SharedUse,
    UnsupportedCapability,
)
from squid_ui.planning.offers import OfferPolicy, semantic_offers
from squid_ui.planning.realizations import (
    ActionsRealization,
    CardRealization,
    ClassicContentRealization,
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
    matrix_text,
    record_text,
)
from squid_ui.planning.resources import Axis, ResourceCost


def embed_text_size(embed: scene.Embed) -> int:
    return (
        len(embed.title or "")
        + len(embed.description or "")
        + sum(len(field.name) + len(field.value) for field in embed.fields)
        + (len(embed.footer.text) if embed.footer else 0)
        + (len(embed.author.name) if embed.author else 0)
    )


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
            OfferPolicy(
                table_forms=(TableForm.RECORDS, TableForm.MATRIX),
                panels=True,
                nested_panels=False,
                text_limit=min(self.limits.content, self.limits.embeds.description),
                native_controls=True,
                panel_text_limit=self.limits.embeds.description,
                grid_columns=self.limits.components.row_buttons,
                embed_limits=asdict(self.limits.embeds),
            ),
        )

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.ClassicMessage]:
        content: list[str] = []
        texts: list[str] = []
        embeds: list[scene.Embed] = []
        rows: list[scene.ClassicRow] = []
        uses = [
            SharedUse(
                ResourceIdentity("attachment", asset.key),
                asset,
                ResourceCost({Axis.ATTACHMENTS: int(asset.url is None)}),
            )
            for asset in context.assets
        ]
        violations: list[CandidateViolation] = []
        effects: list[FidelityEffect] = []
        interactions: set[SemanticRef] = set()
        scopes: dict[OccurrenceId, ResourceCost] = {}
        scoped_resources: dict[OccurrenceId, frozenset[ResourceIdentity]] = {}
        field_stack: list[list[scene.EmbedField]] = [[]]
        native_contents = 0

        def field_text(fields: list[scene.EmbedField] | tuple[scene.EmbedField, ...]) -> int:
            return sum(len(field.name) + len(field.value) for field in fields)

        def cards(fields: list[scene.EmbedField], base: scene.Embed) -> None:
            size = max(1, self.limits.embeds.fields)
            if fields and self.limits.embeds.fields == 0:
                violations.append(ConstraintViolation(candidate.offer.occurrence.identity, "embed fields unavailable"))
            embeds.extend(
                (base if start == 0 else scene.Embed(colour=base.colour)).model_copy(
                    update={"fields": tuple(fields[start : start + size])}
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
            nonlocal native_contents
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
                case ClassicContentRealization(declarations=declarations):
                    native_contents += 1
                    if embedded or declarations > 1 or native_contents > 1:
                        violations.append(
                            ConstraintViolation(identity, "a message permits one top-level Content field")
                        )
                    for child in part.children:
                        visit(child, output, embedded=embedded)
                case CardRealization(node=card):
                    if embedded:
                        violations.append(ConstraintViolation(identity, "native cards cannot be nested"))
                    slots: dict[str, str] = {}
                    for child in part.children:
                        pieces: list[str] = []
                        visit(child, pieces, embedded=True)
                        slots[child.offer.occurrence.reference.keys[-1]] = "\n\n".join(pieces)
                    if len(embeds) != embed_start or len(rows) != row_start:
                        violations.append(ConstraintViolation(identity, "card descriptions must contain text blocks"))
                    entries = [
                        scene.EmbedField(
                            name=slots[f"@field-{index}-name"],
                            value=slots[f"@field-{index}-value"],
                            inline=field.inline,
                        )
                        for index, field in enumerate(card.fields)
                    ]
                    for name, size, cap in (
                        ("embed.title", len(slots.get("@title", "")), self.limits.embeds.title),
                        ("embed.description", len(slots.get("@description", "")), self.limits.embeds.description),
                        ("embed.footer", len(slots.get("@footer", "")), self.limits.embeds.footer),
                        ("embed.author", len(slots.get("@author", "")), self.limits.embeds.author),
                        ("embed.fields", len(entries), self.limits.embeds.fields),
                    ):
                        limit(name, size, cap)
                    for entry in entries:
                        limit("embed.field_name", len(entry.name), self.limits.embeds.field_name)
                        limit("embed.field_value", len(entry.value), self.limits.embeds.field_value)
                        if not entry.name or not entry.value:
                            violations.append(ConstraintViolation(identity, "embed field needs a name and value"))
                    media: dict[str, scene.EmbedMedia | None] = {}
                    for name, item in (("image", card.image), ("thumbnail", card.thumbnail)):
                        if item is None:
                            media[name] = None
                            continue
                        url = item.url
                        if item.asset_name is not None:
                            matches = [asset for asset in context.assets if asset.name == item.asset_name]
                            if len(matches) != 1:
                                violations.append(
                                    ConstraintViolation(identity, "embed media must match one declared attachment")
                                )
                            else:
                                uses.append(
                                    SharedUse(
                                        ResourceIdentity("attachment", matches[0].key),
                                        matches[0],
                                        ResourceCost({Axis.ATTACHMENTS: 1}),
                                    )
                                )
                            url = f"attachment://{item.asset_name}"
                        media_description = (
                            resolve_text(item.description, context.localization).plain()
                            if item.description is not None
                            else None
                        )
                        media[name] = scene.EmbedMedia(
                            url=url, description=media_description, asset_name=item.asset_name
                        )
                    timestamp = card.timestamp.instant if isinstance(card.timestamp, ZonedDateTime) else card.timestamp
                    embeds.append(
                        scene.Embed(
                            title=slots.get("@title") or None,
                            url=card.url,
                            description=slots.get("@description") or None,
                            fields=tuple(entries),
                            footer=scene.EmbedFooter(text=slots.get("@footer", ""), icon_url=card.footer.icon_url)
                            if card.footer and slots.get("@footer")
                            else None,
                            author=scene.EmbedAuthor(
                                name=slots.get("@author", ""), url=card.author.url, icon_url=card.author.icon_url
                            )
                            if card.author and slots.get("@author")
                            else None,
                            colour=card.accent,
                            image=media["image"],
                            thumbnail=media["thumbnail"],
                            timestamp=timestamp.isoformat() if timestamp else None,
                        )
                    )
                case FieldRealization(label=label, value=value):
                    name, content = markdown(label), markdown(value)
                    limit("embed.field_name", len(name), self.limits.embeds.field_name)
                    limit("embed.field_value", len(content), self.limits.embeds.field_value)
                    if not name or not content:
                        violations.append(ConstraintViolation(identity, "embed field needs a name and value"))
                    entry = scene.EmbedField(name=name, value=content)
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
                    if embedded and part.offer.occurrence.placement is Placement.HEADER and payload.role == "heading":
                        text(markdown(payload.visible_nodes))
                    else:
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
                    if table.form == TableForm.MATRIX:
                        text(matrix_text(table))
                    else:
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
                        title: list[str] = []
                        for index, child in enumerate(part.children):
                            visit(
                                child,
                                title if index == 0 and panel.kind in ("article", "section") else description,
                                embedded=True,
                            )
                        field_stack.pop()
                        heading = "\n".join(title)
                        limit("embed.title", len(heading), self.limits.embeds.title)
                        value = "\n".join(description)
                        limit("embed.description", len(value), self.limits.embeds.description)
                        cards(
                            entries,
                            scene.Embed(
                                title=heading or None,
                                description=value or None,
                                colour=panel.accent,
                                thumbnail=scene.EmbedMedia(url=panel.thumbnail)
                                if panel.thumbnail is not None
                                else None,
                            ),
                        )

                case PremiumRealization(sku_id=sku_id):
                    if Capability.ACTIONS_DISCORD_PREMIUM not in context.capabilities:
                        violations.append(UnsupportedCapability(identity, "Discord premium buttons"))
                    limit("row.buttons", 1, self.limits.components.row_buttons)
                    premium = scene.PremiumButton(sku_id=sku_id)
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
                            control.label or None, control.url or "", emoji=control.emoji, disabled=control.disabled
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
                case ImageRealization() as image:
                    if image.spoiler:
                        violations.append(UnsupportedCapability(identity, "classic embed spoiler image"))
                    asset = image.source if isinstance(image.source, Asset) else None
                    if asset is not None:
                        uses.append(
                            SharedUse(
                                ResourceIdentity("attachment", asset.key),
                                asset,
                                ResourceCost({Axis.ATTACHMENTS: int(asset.url is None)}),
                            )
                        )
                    url = (asset.url or f"attachment://{asset.name}") if asset is not None else str(image.source)
                    limit("embed.description", len(image.description), self.limits.embeds.description)
                    if image.description:
                        texts.append(image.description)
                    embeds.append(
                        scene.Embed(
                            description=image.description or None,
                            image=scene.EmbedMedia(
                                url=url,
                                description=image.description or None,
                                asset_name=asset.name if asset is not None else None,
                            ),
                        )
                    )
                case DownloadRealization() as download:
                    uses.append(
                        SharedUse(
                            ResourceIdentity("attachment", download.asset.key),
                            download.asset,
                            ResourceCost({Axis.ATTACHMENTS: int(download.asset.url is None)}),
                        )
                    )
                    label = markdown(download.label) or download.asset.name
                    text(f"[{label}]({download.asset.url})" if download.asset.url else label)
                    text(markdown(download.description))
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
                        + sum(embed_text_size(item) for item in embeds[embed_start:])
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
                Axis.EMBED_TEXT: sum(embed_text_size(embed) for embed in body.embeds),
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
            or any(
                len(embed.description or "") > self.limits.embeds.description
                or len(embed.title or "") > self.limits.embeds.title
                or (len(embed.footer.text) if embed.footer else 0) > self.limits.embeds.footer
                or (len(embed.author.name) if embed.author else 0) > self.limits.embeds.author
                for embed in body.embeds
            )
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

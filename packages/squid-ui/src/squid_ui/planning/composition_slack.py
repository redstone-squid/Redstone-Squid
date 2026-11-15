"""Direct Slack realization rules; no HTML planning or HTML scene is involved."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from squid_i18n import tree
from squid_ui import scene
from squid_ui import semantic as sem
from squid_ui.assets import Asset
from squid_ui.capabilities import Capability
from squid_ui.entity import ConversationType, EntityType
from squid_ui.errors import DrawInvariantError
from squid_ui.planning._slack_forms import SlackFormRenderer, form_texts
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
    SemanticOccurrence,
    SharedResources,
    SharedUse,
    UnsupportedCapability,
)
from squid_ui.planning.realizations import (
    ActionsRealization,
    ControlRealization,
    DownloadRealization,
    EntityRealization,
    FieldsRealization,
    FlowRealization,
    ImageRealization,
    PanelRealization,
    Realization,
    RowRealization,
    SelectRealization,
    TableForm,
    TableRealization,
    TextRealization,
    TextSequenceRealization,
    TimeRealization,
    record_text,
    semantic_offers,
)
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost, ResourceIdentity
from squid_ui.presentations import FidelityEffect, SemanticRef
from squid_ui.primitives.styles import ActionStyle
from squid_ui.slack.target import SlackHomeLimits, SlackMessageLimits, SlackModalLimits
from squid_ui.text import resolve_text


def _plain(nodes: tuple[tree.Node, ...]) -> str | None:
    parts: list[str] = []
    for node in nodes:
        match node.kind:
            case "text":
                parts.append(node.content)
            case "inline" | "sequence":
                child = _plain(node.children)
                if child is None:
                    return None
                parts.append(child)
            case "softbreak" | "hardbreak":
                parts.append("\n")
            case _:
                return None
    return "".join(parts)


def _mrkdwn(nodes: tuple[tree.Node, ...]) -> str | None:
    """Encode supported inline structure without treating literal punctuation as markup."""
    parts: list[str] = []
    for node in nodes:
        if node.kind == "text":
            if any(marker in node.content for marker in ("*", "_", "~", "`")):
                return None
            parts.append(node.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        elif node.kind in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif node.kind == "code_inline":
            if "`" in node.content:
                return None
            parts.append("`" + node.content + "`")
        else:
            children = _mrkdwn(node.children)
            if children is None:
                return None
            if node.kind in ("inline", "sequence"):
                parts.append(children)
            elif node.kind in ("strong", "em", "s"):
                marker = {"strong": "*", "em": "_", "s": "~"}[node.kind]
                parts.append(marker + children + marker)
            elif node.kind == "link":
                url = dict(node.attrs).get("href", "")
                if not tree.valid_link(url) or any(char in url for char in ("|", ">", "<")):
                    return None
                parts.append(f"<{url}|{children}>")
            else:
                return None
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class SlackCompositionRules[
    RenderTargetT,
    ContextT,
    BodyT: scene.SlackMessage | scene.SlackHomeView | scene.SlackModalView = scene.SlackMessage,
]:
    limits: SlackMessageLimits | SlackHomeLimits | SlackModalLimits = SlackMessageLimits()
    modal: sem.FormTrigger | None = None

    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
        | OfferDiscovery[RenderTargetT, ContextT, Realization]
    ):
        if self.modal is not None and isinstance(
            occurrence.graph.units[occurrence.reference].value, sem.Region | sem.Paged
        ):
            return ()
        discovered = semantic_offers(
            occurrence,
            context,
            table_forms=(TableForm.NATIVE, TableForm.RECORDS),
            panels=False,
            disabled_controls=False,
            text_limit=self.limits.components.section_text,
            heading_limit=self.limits.components.header_text,
            grid_columns=self.limits.components.actions_elements,
        )

        if self.modal is None:
            return discovered
        offers = discovered.offers if isinstance(discovered, OfferDiscovery) else discovered
        offers = tuple(replace(offer, binding=None) if offer.form is not None else offer for offer in offers)
        return replace(discovered, offers=offers) if isinstance(discovered, OfferDiscovery) else offers

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, BodyT]:
        texts: list[str] = []
        scopes: dict[OccurrenceId, ResourceCost] = {}
        violations: list[CandidateViolation] = []
        uses = [SharedUse(ResourceIdentity("attachment", asset.key), asset, EMPTY_COST) for asset in context.assets]
        effects: list[FidelityEffect] = []
        interactions: set[SemanticRef] = set()
        blocks = 0
        prepared_blocks: list[scene.SlackBlock] = []

        def realize(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> None:
            nonlocal blocks
            context.work.consume()
            identity = part.offer.occurrence.identity
            effects.extend(part.offer.effects)
            if part.offer.interaction:
                interactions.update(part.offer.occurrence.sources)

            def limit(name: str, used: int, capacity: int) -> None:
                if used > capacity:
                    violations.append(LocalCapacityExceeded(identity, name, used, capacity))

            def text(nodes: tuple[tree.Node, ...], *, capacity: int, name: str) -> str | None:
                value = _plain(nodes)
                if value is None:
                    violations.append(UnsupportedCapability(identity, "rich Slack text"))
                else:
                    texts.append(value)
                    limit(name, len(value), capacity)
                return value

            match part.offer.payload:
                case ControlRealization() as control:
                    if part.offer.form is not None and isinstance(self.limits, SlackModalLimits):
                        renderer = SlackFormRenderer(self.limits, context.localization, identity)
                        inputs = renderer.blocks(part.offer.form, "/".join(identity.path))
                        prepared_blocks.extend(inputs)
                        texts.extend(value.content for value in form_texts(inputs))
                        violations.extend(renderer.violations)
                        blocks += len(inputs)
                        return
                    if control.disabled:
                        violations.append(UnsupportedCapability(identity, "disabled Slack button"))
                    if not control.label:
                        violations.append(ConstraintViolation(identity, "button needs a label"))
                    limit("button.label", len(control.label), self.limits.components.button_label)
                    limit("button.url", len(control.url or ""), self.limits.components.url)
                    limit("button.id", len(control.action or control.route or ""), self.limits.components.action_id)
                    limit("actions.elements", 1, self.limits.components.actions_elements)
                    style = (
                        scene.SlackButtonStyle.DANGER
                        if control.style == ActionStyle.DANGER
                        else scene.SlackButtonStyle.PRIMARY
                        if control.style in (ActionStyle.PRIMARY, ActionStyle.SUCCESS)
                        else scene.SlackButtonStyle.DEFAULT
                    )
                    prepared_blocks.append(
                        scene.SlackActions(
                            (
                                scene.SlackButton(
                                    scene.SlackText(control.label, scene.SlackTextKind.PLAIN, emoji=False),
                                    action=scene.SlackActionRef(control.action, control.mode)
                                    if control.action is not None
                                    else None,
                                    route=scene.SlackRouteRef(control.route) if control.route is not None else None,
                                    url=control.url,
                                    style=style,
                                ),
                            )
                        )
                    )
                    texts.append(control.label)
                    blocks += 1
                case EntityRealization(node=control):
                    if Capability.ACTIONS_ENTITY not in context.capabilities:
                        violations.append(UnsupportedCapability(identity, "native entity discovery"))
                    supported_conversations = frozenset(
                        (
                            ConversationType.WORKSPACE_PUBLIC,
                            ConversationType.WORKSPACE_PRIVATE,
                            ConversationType.DIRECT,
                            ConversationType.GROUP_DIRECT,
                        )
                    )
                    if control.entity_type not in (EntityType.USER, EntityType.CONVERSATION):
                        violations.append(
                            UnsupportedCapability(identity, f"Slack {control.entity_type.value} discovery")
                        )
                    if not set(control.conversation_types) <= supported_conversations:
                        violations.append(UnsupportedCapability(identity, "Slack conversation family"))
                    if not 0 <= control.min_values <= control.max_values or control.max_values < 1:
                        violations.append(ConstraintViolation(identity, "entity select cardinality"))
                    if len(control.default_values) > control.max_values:
                        violations.append(ConstraintViolation(identity, "entity select default count"))
                    if any(not control.entity_type.supports(value.kind) for value in control.default_values):
                        violations.append(ConstraintViolation(identity, "entity select default kind"))
                    limit("select.values", control.max_values, self.limits.components.select_options)
                    limit("select.placeholder", len(control.placeholder or ""), self.limits.components.placeholder)
                    limit("select.id", len(control.action), self.limits.components.action_id)
                    selector = scene.SlackSelect(
                        action=scene.SlackActionRef(control.action, control.mode),
                        kind=scene.SlackSelectKind.CONVERSATIONS
                        if control.entity_type == EntityType.CONVERSATION
                        else scene.SlackSelectKind.USERS,
                        placeholder=scene.SlackText(control.placeholder, scene.SlackTextKind.PLAIN, emoji=False)
                        if control.placeholder
                        else None,
                        initial_values=tuple(str(value.id) for value in control.default_values),
                        conversation_types=control.conversation_types,
                        minimum=control.min_values,
                        maximum=control.max_values,
                    )
                    prepared_blocks.append(scene.SlackActions((selector,)))
                    blocks += 1
                case SelectRealization(node=control, summaries=summaries):
                    if summaries is not None:
                        nodes = tuple(
                            node
                            for index, summary in enumerate(summaries)
                            for node in ((tree.literal("\n"),) if index else ()) + summary.nodes
                        )
                        overview = _mrkdwn(nodes)
                        if overview is None:
                            violations.append(UnsupportedCapability(identity, "Slack item summary text"))
                            overview = ""
                        limit("section.text", len(overview), self.limits.components.section_text)
                        texts.append(overview)
                        prepared_blocks.append(
                            scene.SlackSection(scene.SlackText(overview, scene.SlackTextKind.MARKDOWN))
                        )
                        blocks += 1
                    if control.disabled:
                        violations.append(UnsupportedCapability(identity, "disabled Slack select"))
                    if not 0 <= control.min_values <= control.max_values <= len(control.options):
                        violations.append(ConstraintViolation(identity, "select cardinality"))
                    limit("select.options", len(control.options), self.limits.components.select_options)
                    limit("select.placeholder", len(control.placeholder or ""), self.limits.components.placeholder)
                    identifier = control.action if isinstance(control, scene.Select) else control.route_id
                    limit("select.id", len(identifier), self.limits.components.action_id)
                    for option in control.options:
                        limit("option.label", len(option.label), self.limits.components.option_label)
                        limit("option.value", len(option.value), self.limits.components.option_value)
                        limit(
                            "option.description",
                            len(option.description or ""),
                            self.limits.components.option_description,
                        )
                        if not option.label or not option.value:
                            violations.append(ConstraintViolation(identity, "select option needs label and value"))
                    options = tuple(
                        scene.SlackOption(
                            scene.SlackText(option.label, scene.SlackTextKind.PLAIN, emoji=False),
                            option.value,
                            scene.SlackText(option.description, scene.SlackTextKind.PLAIN, emoji=False)
                            if option.description is not None
                            else None,
                        )
                        for option in control.options
                    )
                    selector = scene.SlackSelect(
                        action=scene.SlackActionRef(control.action, control.mode)
                        if isinstance(control, scene.Select)
                        else None,
                        route=scene.SlackRouteRef(control.route_id)
                        if isinstance(control, scene.RoutedSelect)
                        else None,
                        placeholder=scene.SlackText(control.placeholder, scene.SlackTextKind.PLAIN, emoji=False)
                        if control.placeholder
                        else None,
                        options=options,
                        initial_values=tuple(option.value for option in control.options if option.default),
                        minimum=control.min_values,
                        maximum=control.max_values,
                    )
                    texts.extend(option.label for option in control.options)
                    prepared_blocks.append(scene.SlackActions((selector,)))
                    blocks += 1
                    for child in part.children:
                        visit(child)
                case TextSequenceRealization() as sequence:
                    nodes = (
                        *tuple(
                            node
                            for index, item in enumerate(sequence.display_items)
                            for node in (
                                tree.literal(
                                    (
                                        sequence.separator
                                        if index and not item.continuation
                                        else sequence.prefix
                                        if not index
                                        else ""
                                    )
                                    + item.prefix
                                ),
                                *item.nodes,
                            )
                        ),
                        tree.literal(sequence.suffix),
                        *((tree.literal("\n"), *sequence.notice) if sequence.notice else ()),
                    )
                    plain = _plain(nodes)
                    rendered = plain if plain is not None else _mrkdwn(nodes)
                    if rendered is None:
                        violations.append(
                            UnsupportedCapability(identity, "Slack text has no lossless mrkdwn representation")
                        )
                    elif rendered:
                        limit("section.text", len(rendered), self.limits.components.section_text)
                        texts.append(rendered)
                        blocks += 1
                        prepared_blocks.append(
                            scene.SlackSection(
                                scene.SlackText(
                                    rendered,
                                    scene.SlackTextKind.PLAIN if plain is not None else scene.SlackTextKind.MARKDOWN,
                                    emoji=False if plain is not None else None,
                                )
                            )
                        )
                    for child in part.children:
                        visit(child)
                case TimeRealization() as time:
                    prefix = _mrkdwn(time.prefix)
                    if prefix is None:
                        violations.append(UnsupportedCapability(identity, "Slack time label markup"))
                    else:
                        display = time.display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        if time.timezone is None:
                            token = {
                                "t": "{time}",
                                "T": "{time_secs}",
                                "d": "{date_num}",
                                "D": "{date_long}",
                                "f": "{date_short_pretty} at {time}",
                                "F": "{date_long_pretty} at {time}",
                                "R": "{ago}",
                            }[time.style or "f"]
                            epoch = int(datetime.fromisoformat(time.instant).timestamp())
                            display = f"<!date^{epoch}^{token}|{display}>"
                        else:
                            display = f"`{time.display}`"
                        rendered = prefix + display
                        limit("text", len(rendered), self.limits.components.section_text)
                        texts.append(rendered)
                        prepared_blocks.append(
                            scene.SlackSection(scene.SlackText(rendered, scene.SlackTextKind.MARKDOWN))
                        )
                        blocks += 1
                case TextRealization() as payload:
                    nodes = payload.visible_nodes
                    if payload.shortening is not None and not nodes:
                        return
                    plain = _plain(nodes)
                    rendered = plain if plain is not None else _mrkdwn(nodes)
                    kind = scene.SlackTextKind.PLAIN if plain is not None else scene.SlackTextKind.MARKDOWN
                    capacity = self.limits.components.section_text
                    if payload.role == "heading":
                        capacity = self.limits.components.header_text
                        if plain is None:
                            violations.append(UnsupportedCapability(identity, "Slack headers require plain text"))
                    elif payload.role == "code":
                        code = "".join(tree.plain(node) for node in nodes)
                        if "```" in code:
                            violations.append(UnsupportedCapability(identity, "embedded Slack code fence"))
                        rendered = "```\n" + code + "\n```"
                        kind = scene.SlackTextKind.MARKDOWN
                    elif payload.role == "quote":
                        quoted = _mrkdwn(nodes)
                        rendered = (
                            "\n".join("> " + line for line in quoted.splitlines()) if quoted is not None else None
                        )
                        kind = scene.SlackTextKind.MARKDOWN
                    if rendered is None:
                        violations.append(
                            UnsupportedCapability(identity, "Slack text has no lossless mrkdwn representation")
                        )
                    elif rendered:
                        limit("text", len(rendered), capacity)
                        texts.append(rendered)
                        value = scene.SlackText(
                            rendered, kind, emoji=False if kind == scene.SlackTextKind.PLAIN else None
                        )
                        prepared_blocks.append(
                            scene.SlackHeader(value)
                            if payload.role == "heading"
                            else scene.SlackContext((value,))
                            if payload.role == "note"
                            else scene.SlackSection(value)
                        )
                        blocks += 1
                case TableRealization() as table:
                    context.work.consume(len(table.columns) + sum(1 + len(row.cells) for row in table.rows))
                    if table.form == TableForm.NATIVE:
                        blocks += 1
                        limit("table.columns", len(table.columns), self.limits.components.table_columns)
                        limit("table.rows", len(table.rows) + 1, self.limits.components.table_rows)
                        start = len(texts)
                        prepared_rows: list[tuple[scene.SlackText, ...]] = []
                        for row in (table.columns, *(row.cells for row in table.rows)):
                            cells: list[scene.SlackText] = []
                            for cell in row:
                                value = text(cell.nodes, capacity=self.limits.components.table_text, name="table.text")
                                if value is not None:
                                    cells.append(scene.SlackText(value, scene.SlackTextKind.PLAIN, emoji=False))
                            prepared_rows.append(tuple(cells))
                        prepared_blocks.append(scene.SlackTable(tuple(prepared_rows)))
                        limit(
                            "table.text", sum(len(value) for value in texts[start:]), self.limits.components.table_text
                        )
                    else:
                        for nodes in record_text(table):
                            rendered = text(nodes, capacity=self.limits.components.section_text, name="section.text")
                            blocks += int(bool(rendered))
                            if rendered == "":
                                texts.pop()
                            elif rendered is not None:
                                prepared_blocks.append(
                                    scene.SlackSection(
                                        scene.SlackText(rendered, scene.SlackTextKind.PLAIN, emoji=False)
                                    )
                                )
                case ActionsRealization(columns=columns):
                    start = len(prepared_blocks)
                    packed: list[scene.SlackBlock] = []
                    pending: list[scene.SlackButton | scene.SlackSelect] = []
                    size = max(1, columns if columns is not None else self.limits.components.actions_elements)
                    if columns is not None:
                        limit("grid.columns", columns, self.limits.components.actions_elements)

                    def flush() -> None:
                        if pending:
                            packed.append(scene.SlackActions(tuple(pending)))
                            pending.clear()

                    for child in part.children:
                        before = len(prepared_blocks)
                        visit(child)
                        if isinstance(child.offer.payload, ControlRealization):
                            for block in prepared_blocks[before:]:
                                if isinstance(block, scene.SlackActions):
                                    pending.extend(block.elements)
                            if len(pending) == size:
                                flush()
                        else:
                            flush()
                            packed.extend(prepared_blocks[before:])
                    flush()
                    blocks += len(packed) - (len(prepared_blocks) - start)
                    prepared_blocks[start:] = packed
                case RowRealization():
                    start = len(prepared_blocks)
                    for child in part.children:
                        visit(child)
                    group = prepared_blocks[start:]
                    if any(not isinstance(block, scene.SlackActions) for block in group):
                        violations.append(UnsupportedCapability(identity, "non-button Slack action group"))
                    else:
                        elements = tuple(
                            element
                            for block in group
                            if isinstance(block, scene.SlackActions)
                            for element in block.elements
                        )
                        if isinstance(part.offer.payload, RowRealization):
                            limit("actions.elements", len(elements), self.limits.components.actions_elements)
                        size = max(1, self.limits.components.actions_elements)
                        packed = [scene.SlackActions(elements[i : i + size]) for i in range(0, len(elements), size)]
                        prepared_blocks[start:] = packed
                        blocks += len(packed) - len(group)
                case DownloadRealization() as download:
                    label = text(
                        download.label or resolve_text(context.chrome.download, context.localization).nodes,
                        capacity=self.limits.components.button_label,
                        name="download.label",
                    )
                    if download.spoiler:
                        violations.append(UnsupportedCapability(identity, "Slack download spoilers"))
                    uses.append(
                        SharedUse(ResourceIdentity("attachment", download.asset.key), download.asset, EMPTY_COST)
                    )
                    prepared_blocks.append(
                        scene.SlackActions(
                            (
                                scene.SlackButton(
                                    scene.SlackText(label or ""),
                                    asset=scene.SlackAssetRef(
                                        download.asset.key, download.asset.name, download.asset.media_type
                                    ),
                                ),
                            )
                        )
                    )
                    blocks += 1
                    if download.description:
                        description = _mrkdwn(download.description)
                        if description is None:
                            violations.append(UnsupportedCapability(identity, "download description markup"))
                        else:
                            limit("download.description", len(description), self.limits.components.section_text)
                            texts.append(description)
                            prepared_blocks.append(
                                scene.SlackSection(scene.SlackText(description, scene.SlackTextKind.MARKDOWN))
                            )
                            blocks += 1
                case ImageRealization() as media:
                    if isinstance(media.source, Asset) or media.spoiler:
                        violations.append(
                            UnsupportedCapability(identity, "Slack images require public URLs and cannot hide spoilers")
                        )
                    else:
                        limit("image.url", len(media.source), self.limits.components.url)
                        texts.append(media.description)
                        prepared_blocks.append(scene.SlackImage(media.source, media.description))
                        blocks += 1
                case PanelRealization() as panel:
                    if panel.framed:
                        violations.append(UnsupportedCapability(identity, "Slack panel framing"))
                    if panel.accent is not None:
                        violations.append(UnsupportedCapability(identity, "Slack section accent"))
                    for index, child in enumerate(part.children):
                        visit(child)
                        if index == 0 and panel.thumbnail is not None:
                            limit("image.url", len(panel.thumbnail), self.limits.components.url)
                            prepared_blocks.append(scene.SlackImage(panel.thumbnail, panel.heading))
                            texts.append(panel.heading)
                            blocks += 1
                case FieldsRealization(notice=notice):
                    for child in part.children:
                        visit(child)
                    if notice:
                        rendered_notice = _mrkdwn(notice)
                        if rendered_notice is None:
                            violations.append(UnsupportedCapability(identity, "Slack spill notice markup"))
                        else:
                            limit("text", len(rendered_notice), self.limits.components.section_text)
                            texts.append(rendered_notice)
                            prepared_blocks.append(
                                scene.SlackSection(scene.SlackText(rendered_notice, scene.SlackTextKind.MARKDOWN))
                            )
                            blocks += 1
                case FlowRealization():
                    for child in part.children:
                        visit(child)
                case _:
                    violations.append(UnsupportedCapability(identity, type(part.offer.payload).__name__))

        def visit(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> None:
            before = blocks
            realize(part)
            scopes[part.offer.occurrence.identity] = ResourceCost({Axis.BLOCKS: blocks - before})

        visit(candidate)
        cost = ResourceCost({Axis.BLOCKS: blocks})
        violations.extend(
            CapacityExceeded(candidate.offer.occurrence.identity, axis, used, capacity)
            for axis, used, capacity in cost.over(self.limits.capacities)
        )
        fallback_length = len("\n".join(texts))
        if isinstance(self.limits, SlackMessageLimits) and fallback_length > self.limits.fallback_text:
            violations.append(
                LocalCapacityExceeded(
                    candidate.offer.occurrence.identity, "fallback.text", fallback_length, self.limits.fallback_text
                )
            )
        if isinstance(self.limits, SlackModalLimits):
            assert self.modal is not None
            renderer = SlackFormRenderer(self.limits, context.localization, candidate.offer.occurrence.identity)
            title = renderer.plain(
                resolve_text(self.modal.spec.title, context.localization).content,
                "modal.title",
                capacity=self.limits.title,
            )
            submit = renderer.plain(
                resolve_text(self.modal.label, context.localization).content,
                "modal.submit",
                capacity=self.limits.submit,
            )
            close = renderer.plain(
                resolve_text(context.chrome.cancel, context.localization).content,
                "modal.close",
                capacity=self.limits.close,
            )
            if len(self.modal.key) > self.limits.callback_id:
                violations.append(
                    LocalCapacityExceeded(
                        candidate.offer.occurrence.identity,
                        "modal.callback_id",
                        len(self.modal.key),
                        self.limits.callback_id,
                    )
                )
            violations.extend(renderer.violations)
            body: scene.SlackMessage | scene.SlackHomeView | scene.SlackModalView = scene.SlackModalView(
                self.modal.key, title, submit, close, tuple(prepared_blocks)
            )
        elif isinstance(self.limits, SlackMessageLimits):
            body = scene.SlackMessage("\n".join(texts), tuple(prepared_blocks))
        else:
            body = scene.SlackHomeView(tuple(prepared_blocks))
        return Evaluation(
            cost,
            resources=SharedResources.combine(uses),
            violations=tuple(violations),
            effects=tuple(effects),
            interactions=frozenset(interactions),
            preparation=cast(BodyT, body),
            text=tuple(texts),
            resource_axes=frozenset({Axis.BLOCKS}),
            scoped_costs=scopes,
        )

    def build(self, preparation: BodyT, context: PlanningContext) -> BodyT:
        context.work.consume()
        return preparation

    def validate(self, body: BodyT, expected: Evaluation[Asset, BodyT], context: PlanningContext) -> None:
        texts: list[str] = []

        def text(value: scene.SlackText, capacity: int) -> None:
            if len(value.content) > capacity:
                message = "built Slack text violates its selected realization"
                raise DrawInvariantError(message)
            texts.append(value.content)

        for block in body.blocks:
            context.work.consume()
            match block:
                case scene.SlackInput():
                    if (
                        not isinstance(self.limits, SlackModalLimits)
                        or len(block.block_id) > self.limits.components.block_id
                    ):
                        message = "invalid Slack form input placement or identifier"
                        raise DrawInvariantError(message)
                    for value in form_texts(block):
                        text(value, 2000)
                case scene.SlackActions(elements=elements):
                    if len(elements) > self.limits.components.actions_elements:
                        msg = "built Slack actions exceed their limit"
                        raise DrawInvariantError(msg)
                    for element in elements:
                        if isinstance(element, scene.SlackSelect):
                            if len(element.options) > self.limits.components.select_options:
                                message = "built Slack select exceeds its option limit"
                                raise DrawInvariantError(message)
                            for option in element.options:
                                text(option.label, self.limits.components.option_label)
                            continue
                        if not isinstance(element, scene.SlackButton) or not element.label.content:
                            msg = "unexpected Slack action element"
                            raise DrawInvariantError(msg)
                        text(element.label, self.limits.components.button_label)
                        identifier = (
                            element.action.action
                            if element.action is not None
                            else element.route.route_id
                            if element.route is not None
                            else ""
                        )
                        if (
                            len(identifier) > self.limits.components.action_id
                            or len(element.url or "") > self.limits.components.url
                        ):
                            msg = "built Slack button violates its destination limit"
                            raise DrawInvariantError(msg)
                case scene.SlackSection(text=value) if value is not None:
                    text(value, self.limits.components.section_text)
                    if block.fields or block.accessory is not None:
                        message = "unexpected content in built Slack section"
                        raise DrawInvariantError(message)
                case scene.SlackHeader(text=value):
                    if value.kind != scene.SlackTextKind.PLAIN:
                        message = "Slack headers require plain text"
                        raise DrawInvariantError(message)
                    text(value, self.limits.components.header_text)
                case scene.SlackContext(elements=elements):
                    if len(elements) > self.limits.components.context_elements:
                        message = "Slack context exceeds its element capacity"
                        raise DrawInvariantError(message)
                    for value in elements:
                        text(value, self.limits.components.section_text)
                case scene.SlackImage(alt_text=description):
                    texts.append(description)
                case scene.SlackTable(rows=rows):
                    if len(rows) > self.limits.components.table_rows or any(
                        len(row) > self.limits.components.table_columns for row in rows
                    ):
                        message = "built Slack table exceeds its local limits"
                        raise DrawInvariantError(message)
                    if sum(len(cell.content) for row in rows for cell in row) > self.limits.components.table_text:
                        message = "built Slack table exceeds its aggregate text limit"
                        raise DrawInvariantError(message)
                    for row in rows:
                        for cell in row:
                            text(cell, self.limits.components.table_text)
                case _:
                    message = "unexpected block in shared Slack composition"
                    raise DrawInvariantError(message)
        if isinstance(body, scene.SlackModalView) and (
            not isinstance(self.limits, SlackModalLimits)
            or len(body.callback_id) > self.limits.callback_id
            or len(body.title.content) > self.limits.title
            or (body.submit is not None and len(body.submit.content) > self.limits.submit)
            or (body.close is not None and len(body.close.content) > self.limits.close)
        ):
            message = "built Slack modal exceeds its header limits"
            raise DrawInvariantError(message)
        cost = ResourceCost({Axis.BLOCKS: len(body.blocks)})
        if (
            cost != expected.cost
            or body != expected.preparation
            or not cost.within(self.limits.capacities)
            or tuple(texts) != expected.text
            or (
                isinstance(body, scene.SlackMessage)
                and isinstance(self.limits, SlackMessageLimits)
                and (body.text != "\n".join(texts) or len(body.text) > self.limits.fallback_text)
            )
        ):
            message = "built Slack composition disagrees with evaluation"
            raise DrawInvariantError(message)

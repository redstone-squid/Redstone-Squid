"""Direct Slack realization rules; no HTML planning or HTML scene is involved."""

from dataclasses import dataclass
from typing import cast

from squid_i18n import tree
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    CapacityExceeded,
    ConstraintViolation,
    Evaluation,
    LocalCapacityExceeded,
    OccurrenceId,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    UnsupportedCapability,
)
from squid_ui.planning.realizations import (
    ActionsRealization,
    ControlRealization,
    FlowRealization,
    ImageRealization,
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
from squid_ui.primitives.styles import ActionStyle
from squid_ui.slack.target import SlackHomeLimits, SlackMessageLimits


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
    BodyT: scene.SlackMessage | scene.SlackHomeView = scene.SlackMessage,
]:
    limits: SlackMessageLimits | SlackHomeLimits = SlackMessageLimits()

    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]:
        return semantic_offers(
            occurrence,
            context,
            table_forms=(TableForm.NATIVE, TableForm.RECORDS),
            panels=False,
            disabled_controls=False,
            text_limit=self.limits.components.section_text,
        )

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, BodyT]:
        texts: list[str] = []
        scopes: dict[OccurrenceId, ResourceCost] = {}
        violations: list[CandidateViolation] = (
            [UnsupportedCapability(candidate.offer.occurrence.identity, "Slack document attachments")]
            if context.assets
            else []
        )
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
                case SelectRealization(node=control):
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
                case TextSequenceRealization() as sequence:
                    nodes = (
                        *tuple(
                            node
                            for index, item in enumerate(sequence.items)
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
                case TextRealization() as payload:
                    plain = _plain(payload.nodes)
                    rendered = plain if plain is not None else _mrkdwn(payload.nodes)
                    kind = scene.SlackTextKind.PLAIN if plain is not None else scene.SlackTextKind.MARKDOWN
                    capacity = self.limits.components.section_text
                    if payload.role == "heading":
                        capacity = self.limits.components.header_text
                        if plain is None:
                            violations.append(UnsupportedCapability(identity, "Slack headers require plain text"))
                    elif payload.role == "code":
                        code = "".join(tree.plain(node) for node in payload.nodes)
                        if "```" in code:
                            violations.append(UnsupportedCapability(identity, "embedded Slack code fence"))
                        rendered = "```\n" + code + "\n```"
                        kind = scene.SlackTextKind.MARKDOWN
                    elif payload.role == "quote":
                        quoted = _mrkdwn(payload.nodes)
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
                case ActionsRealization():
                    start = len(prepared_blocks)
                    packed: list[scene.SlackBlock] = []
                    pending: list[scene.SlackButton | scene.SlackSelect] = []
                    size = max(1, self.limits.components.actions_elements)

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
        return Evaluation(
            cost,
            violations=tuple(violations),
            effects=tuple(effects),
            interactions=frozenset(interactions),
            preparation=cast(
                BodyT,
                scene.SlackMessage("\n".join(texts), tuple(prepared_blocks))
                if isinstance(self.limits, SlackMessageLimits)
                else scene.SlackHomeView(tuple(prepared_blocks)),
            ),
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

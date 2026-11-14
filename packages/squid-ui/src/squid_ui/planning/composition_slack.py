"""Direct Slack realization rules; no HTML planning or HTML scene is involved."""

from dataclasses import dataclass

from squid_i18n import tree
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    CapacityExceeded,
    Evaluation,
    LocalCapacityExceeded,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    UnsupportedCapability,
)
from squid_ui.planning.realizations import (
    FlowRealization,
    Realization,
    TableForm,
    TableRealization,
    TextRealization,
    record_text,
    semantic_offers,
)
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentations import FidelityEffect
from squid_ui.slack.target import SlackMessageLimits


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


@dataclass(frozen=True, slots=True)
class SlackCompositionRules[RenderTargetT, ContextT]:
    limits: SlackMessageLimits = SlackMessageLimits()

    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]:
        return semantic_offers(occurrence, context, table_forms=(TableForm.NATIVE, TableForm.RECORDS), panels=False)

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.SlackMessage]:
        texts: list[str] = []
        violations: list[CandidateViolation] = []
        effects: list[FidelityEffect] = []
        blocks = 0
        expected_blocks: list[scene.SlackBlock] = []

        def visit(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> None:
            nonlocal blocks
            context.work.consume()
            identity = part.offer.occurrence.identity
            effects.extend(part.offer.effects)

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
                case TextRealization(nodes=nodes):
                    rendered = text(nodes, capacity=self.limits.components.section_text, name="section.text")
                    blocks += int(bool(rendered))
                    if rendered == "":
                        texts.pop()
                    elif rendered is not None:
                        expected_blocks.append(
                            scene.SlackSection(scene.SlackText(rendered, scene.SlackTextKind.PLAIN, emoji=False))
                        )
                case TableRealization() as table:
                    if table.form == TableForm.NATIVE:
                        blocks += 1
                        limit("table.columns", len(table.columns), self.limits.components.table_columns)
                        limit("table.rows", len(table.rows) + 1, self.limits.components.table_rows)
                        start = len(texts)
                        for row in (table.columns, *(row.cells for row in table.rows)):
                            for cell in row:
                                text(cell.nodes, capacity=self.limits.components.table_text, name="table.text")
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
                                expected_blocks.append(
                                    scene.SlackSection(
                                        scene.SlackText(rendered, scene.SlackTextKind.PLAIN, emoji=False)
                                    )
                                )
                case FlowRealization():
                    for child in part.children:
                        visit(child)
                case _:
                    violations.append(UnsupportedCapability(identity, type(part.offer.payload).__name__))

        visit(candidate)
        cost = ResourceCost({Axis.BLOCKS: blocks})
        violations.extend(
            CapacityExceeded(candidate.offer.occurrence.identity, axis, used, capacity)
            for axis, used, capacity in cost.over(self.limits.capacities)
        )
        fallback_length = len("\n".join(texts))
        if fallback_length > self.limits.fallback_text:
            violations.append(
                LocalCapacityExceeded(
                    candidate.offer.occurrence.identity, "fallback.text", fallback_length, self.limits.fallback_text
                )
            )
        return Evaluation(
            cost,
            violations=tuple(violations),
            effects=tuple(effects),
            evidence=scene.SlackMessage("\n".join(texts), tuple(expected_blocks)),
            text=tuple(texts),
        )

    def build(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> scene.SlackMessage:
        texts: list[str] = []

        def text(nodes: tuple[tree.Node, ...]) -> scene.SlackText:
            content = _plain(nodes)
            if content is None:
                message = "selected Slack text has no supported realization"
                raise DrawInvariantError(message)
            texts.append(content)
            return scene.SlackText(content, scene.SlackTextKind.PLAIN, emoji=False)

        def section(nodes: tuple[tree.Node, ...]) -> tuple[scene.SlackBlock, ...]:
            value = text(nodes)
            if not value.content:
                texts.pop()
                return ()
            return (scene.SlackSection(value),)

        def visit(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> tuple[scene.SlackBlock, ...]:
            context.work.consume()
            match part.offer.payload:
                case TextRealization(nodes=nodes):
                    return section(nodes)
                case TableRealization() as table:
                    if table.form == TableForm.NATIVE:
                        rows = (table.columns, *(row.cells for row in table.rows))
                        return (scene.SlackTable(tuple(tuple(text(cell.nodes) for cell in row) for row in rows)),)
                    return tuple(block for row in record_text(table) for block in section(row))
                case FlowRealization():
                    return tuple(block for child in part.children for block in visit(child))
                case _:
                    message = "unsupported selected Slack realization"
                    raise DrawInvariantError(message)

        blocks = visit(candidate)
        return scene.SlackMessage("\n".join(texts), blocks)

    def validate(
        self, body: scene.SlackMessage, expected: Evaluation[Asset, scene.SlackMessage], context: PlanningContext
    ) -> None:
        texts: list[str] = []

        def text(value: scene.SlackText, capacity: int) -> None:
            if value.kind != scene.SlackTextKind.PLAIN or len(value.content) > capacity:
                message = "built Slack text violates its selected realization"
                raise DrawInvariantError(message)
            texts.append(value.content)

        for block in body.blocks:
            context.work.consume()
            match block:
                case scene.SlackSection(text=value) if value is not None:
                    text(value, self.limits.components.section_text)
                    if block.fields or block.accessory is not None:
                        message = "unexpected content in built Slack section"
                        raise DrawInvariantError(message)
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
            or body != expected.evidence
            or not cost.within(self.limits.capacities)
            or tuple(texts) != expected.text
            or body.text != "\n".join(texts)
            or len(body.text) > self.limits.fallback_text
        ):
            message = "built Slack composition disagrees with evaluation"
            raise DrawInvariantError(message)

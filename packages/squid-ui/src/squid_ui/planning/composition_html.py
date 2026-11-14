"""Unbounded HTML realization through the shared composition contract."""

from dataclasses import dataclass
from typing import assert_never

from squid_i18n import tree
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    Evaluation,
    PlanningContext,
    RealizationOffer,
    ResourceIdentity,
    SemanticOccurrence,
    SharedResources,
    SharedUse,
    UnsupportedCapability,
)
from squid_ui.planning.realizations import (
    DownloadRealization,
    FlowRealization,
    PanelRealization,
    Realization,
    TableForm,
    TableRealization,
    TextRealization,
    record_text,
    semantic_offers,
)
from squid_ui.planning.resources import EMPTY_COST
from squid_ui.planning.text_nodes import html_text
from squid_ui.presentations import FidelityEffect

_INLINE = frozenset(
    {"text", "inline", "sequence", "strong", "em", "s", "code_inline", "link", "softbreak", "hardbreak"}
)


def _inline_supported(nodes: tuple[tree.Node, ...], context: PlanningContext) -> bool:
    for node in nodes:
        context.work.consume()
        if node.kind not in _INLINE or not _inline_supported(node.children, context):
            return False
    return True


def _text_leaves(node: scene.HtmlNode) -> tuple[str, ...]:
    match node:
        case scene.HtmlText(content=content):
            return (content,)
        case scene.HtmlElement(children=children):
            return tuple(text for child in children for text in _text_leaves(child))


@dataclass(frozen=True, slots=True)
class HtmlCompositionRules[RenderTargetT, ContextT]:
    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]:
        return semantic_offers(occurrence, context, table_forms=(TableForm.NATIVE, TableForm.RECORDS), panels=True)

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.HtmlBody]:
        texts: list[str] = []
        uses: list[SharedUse[Asset]] = []
        violations: list[CandidateViolation] = []
        effects: list[FidelityEffect] = []

        def visit(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> tuple[scene.HtmlNode, ...]:
            context.work.consume()
            effects.extend(part.offer.effects)

            def text(nodes: tuple[tree.Node, ...]) -> scene.HtmlNode:
                if not _inline_supported(nodes, context):
                    violations.append(
                        UnsupportedCapability(part.offer.occurrence.identity, "HTML block or entity text")
                    )
                    return scene.HtmlText("")
                result = html_text(nodes)
                texts.extend(_text_leaves(result))
                return result

            match part.offer.payload:
                case TextRealization(nodes=nodes):
                    return (scene.HtmlElement(scene.HtmlTag.P, (text(nodes),)),)
                case TableRealization() as table:
                    if table.form == TableForm.RECORDS:
                        return tuple(scene.HtmlElement(scene.HtmlTag.P, (text(nodes),)) for nodes in record_text(table))
                    heading = scene.HtmlElement(
                        scene.HtmlTag.TR,
                        tuple(scene.HtmlElement(scene.HtmlTag.TH, (text(column.nodes),)) for column in table.columns),
                    )
                    rows = tuple(
                        scene.HtmlElement(
                            scene.HtmlTag.TR,
                            tuple(scene.HtmlElement(scene.HtmlTag.TD, (text(cell.nodes),)) for cell in row.cells),
                        )
                        for row in table.rows
                    )
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.TABLE,
                            (
                                scene.HtmlElement(scene.HtmlTag.THEAD, (heading,)),
                                scene.HtmlElement(scene.HtmlTag.TBODY, rows),
                            ),
                        ),
                    )
                case FlowRealization():
                    return tuple(node for child in part.children for node in visit(child))
                case PanelRealization(accent=accent):
                    children = tuple(node for child in part.children for node in visit(child))
                    colour = scene.HtmlColourRef(accent) if accent is not None else None
                    return (scene.HtmlElement(scene.HtmlTag.DIV, children, colour=colour),)
                case DownloadRealization() as download:
                    asset = download.asset
                    uses.append(SharedUse(ResourceIdentity("attachment", asset.key), asset, EMPTY_COST))
                    label = download.label or (tree.literal(asset.name),)
                    link = scene.HtmlElement(
                        scene.HtmlTag.A,
                        (text(label),),
                        attributes=(
                            scene.HtmlAttribute(scene.HtmlAttributeName.DOWNLOAD, asset.name),
                            scene.HtmlAttribute(
                                scene.HtmlAttributeName.CLASS,
                                "squid-download squid-spoiler" if download.spoiler else "squid-download",
                            ),
                        ),
                        asset=scene.HtmlAssetRef(asset.key, asset.name, asset.media_type),
                    )
                    return (
                        (link, scene.HtmlElement(scene.HtmlTag.P, (text(download.description),)))
                        if download.description
                        else (link,)
                    )
                case unknown:
                    assert_never(unknown)

        body = scene.HtmlBody(visit(candidate), locale=context.localization.locale)
        return Evaluation(
            resources=SharedResources.combine(uses),
            violations=tuple(violations),
            effects=tuple(effects),
            preparation=body,
            text=tuple(texts),
        )

    def build(self, preparation: scene.HtmlBody, context: PlanningContext) -> scene.HtmlBody:
        context.work.consume()
        return preparation

    def validate(
        self, body: scene.HtmlBody, expected: Evaluation[Asset, scene.HtmlBody], context: PlanningContext
    ) -> None:
        texts: list[str] = []
        assets: set[ResourceIdentity] = set()

        def visit(node: scene.HtmlNode) -> None:
            context.work.consume()
            match node:
                case scene.HtmlText(content=content):
                    texts.append(content)
                case scene.HtmlElement():
                    if node.asset is not None:
                        identity = ResourceIdentity("attachment", node.asset.key)
                        use = expected.resources.uses.get(identity)
                        if use is None or (node.asset.name, node.asset.media_type) != (
                            use.value.name,
                            use.value.media_type,
                        ):
                            message = "HTML download disagrees with its declared asset"
                            raise DrawInvariantError(message)
                        assets.add(identity)
                    for child in node.children:
                        visit(child)

        for node in body.children:
            visit(node)
        if (
            body != expected.preparation
            or tuple(texts) != expected.text
            or assets != expected.resources.uses.keys()
            or expected.cost != EMPTY_COST
        ):
            message = "built HTML composition disagrees with evaluation"
            raise DrawInvariantError(message)

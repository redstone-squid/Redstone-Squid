"""Unbounded HTML realization through the shared composition contract."""

from dataclasses import dataclass, replace

from squid_i18n import tree
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    ConstraintViolation,
    Evaluation,
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
from squid_ui.planning.realizations import (
    ActionsRealization,
    ControlRealization,
    DisclosureRealization,
    DownloadRealization,
    EntityRealization,
    FieldsRealization,
    FlowRealization,
    ImageRealization,
    MetricRealization,
    PanelRealization,
    ProgressRealization,
    Realization,
    RowRealization,
    SelectRealization,
    StatusRealization,
    TableForm,
    TableRealization,
    TextRealization,
    TextSequenceRealization,
    TimeRealization,
    record_text,
    semantic_offers,
)
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.planning.text_nodes import html_text
from squid_ui.presentations import FidelityEffect, SemanticRef

_INLINE = frozenset(
    {
        "text",
        "inline",
        "sequence",
        "strong",
        "em",
        "s",
        "code_inline",
        "link",
        "softbreak",
        "hardbreak",
        "heading",
        "small",
        "blockquote",
        "fence",
    }
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
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
        | OfferDiscovery[RenderTargetT, ContextT, Realization]
    ):
        return semantic_offers(
            occurrence, context, table_forms=(TableForm.NATIVE, TableForm.RECORDS), panels=True, character_budgets=True
        )

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.HtmlBody]:
        texts: list[str] = []
        uses: list[SharedUse[Asset]] = [
            SharedUse(ResourceIdentity("attachment", asset.key), asset, EMPTY_COST) for asset in context.assets
        ]
        violations: list[CandidateViolation] = []
        effects: list[FidelityEffect] = []
        interactions: set[SemanticRef] = set()
        scopes: dict[OccurrenceId, ResourceCost] = {}

        def visit(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> tuple[scene.HtmlNode, ...]:
            start = len(texts)
            rendered = render(part)
            context.work.consume(1 + len(texts) - start)
            scopes[part.offer.occurrence.identity] = ResourceCost({Axis.DISPLAY_TEXT: sum(map(len, texts[start:]))})
            return rendered

        def render(part: CandidateComposition[RenderTargetT, ContextT, Realization]) -> tuple[scene.HtmlNode, ...]:
            context.work.consume()
            effects.extend(part.offer.effects)
            if part.offer.interaction:
                interactions.update(part.offer.occurrence.sources)

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
                case ControlRealization() as control:
                    texts.append(control.label)
                    attributes = (
                        (scene.HtmlAttribute(scene.HtmlAttributeName.DISABLED, "disabled"),) if control.disabled else ()
                    )
                    if control.current:
                        attributes += (scene.HtmlAttribute(scene.HtmlAttributeName.ARIA_CURRENT, "page"),)
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.A if control.url is not None else scene.HtmlTag.BUTTON,
                            (scene.HtmlText(control.label),),
                            attributes=attributes,
                            action=scene.HtmlActionRef(control.action, control.mode)
                            if control.action is not None
                            else None,
                            route=scene.HtmlRouteRef(control.route) if control.route is not None else None,
                            url=scene.HtmlUrlRef(control.url) if control.url is not None else None,
                        ),
                    )
                case EntityRealization(node=control, choices=choices):
                    identity = part.offer.occurrence.identity
                    if not 0 <= control.min_values <= control.max_values or control.max_values < 1:
                        violations.append(ConstraintViolation(identity, "entity select cardinality"))
                    if len(control.default_values) > control.max_values:
                        violations.append(ConstraintViolation(identity, "entity select default count"))
                    if any(not control.entity_type.supports(value.kind) for value in control.default_values):
                        violations.append(ConstraintViolation(identity, "entity select default kind"))
                    metadata = (
                        scene.HtmlAttribute(scene.HtmlAttributeName.NAME, control.action),
                        scene.HtmlAttribute(scene.HtmlAttributeName.ENTITY_TYPE, control.entity_type.value),
                        scene.HtmlAttribute(
                            scene.HtmlAttributeName.CONVERSATION_TYPES,
                            ",".join(value.value for value in control.conversation_types),
                        ),
                        scene.HtmlAttribute(scene.HtmlAttributeName.SELECTION_MIN, control.min_values),
                        scene.HtmlAttribute(scene.HtmlAttributeName.SELECTION_MAX, control.max_values),
                        scene.HtmlAttribute(scene.HtmlAttributeName.ARIA_LABEL, control.placeholder or control.action),
                    )
                    children = tuple(
                        scene.HtmlElement(
                            scene.HtmlTag.OPTION,
                            (text((tree.literal(option.label),)),),
                            attributes=(
                                scene.HtmlAttribute(scene.HtmlAttributeName.VALUE, option.value),
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.SELECTED, value=True),)
                                    if option.default
                                    else ()
                                ),
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.TITLE, option.description),)
                                    if option.description is not None
                                    else ()
                                ),
                            ),
                        )
                        for option in choices
                    )
                    attributes = (
                        (
                            (scene.HtmlAttribute(scene.HtmlAttributeName.MULTIPLE, value=True),)
                            if control.max_values > 1
                            else ()
                        )
                        if choices
                        else (
                            scene.HtmlAttribute(scene.HtmlAttributeName.TYPE, "text"),
                            scene.HtmlAttribute(
                                scene.HtmlAttributeName.VALUE,
                                ",".join(value.encode() for value in control.default_values),
                            ),
                            *(
                                (scene.HtmlAttribute(scene.HtmlAttributeName.PLACEHOLDER, control.placeholder),)
                                if control.placeholder is not None
                                else ()
                            ),
                        )
                    )
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.SELECT if choices else scene.HtmlTag.INPUT,
                            children,
                            attributes=metadata + attributes,
                            action=scene.HtmlActionRef(control.action, control.mode),
                            form=scene.HtmlFormRef(control.action, control.action),
                        ),
                    )
                case SelectRealization(node=control, navigation=navigation, summaries=summaries):
                    overview = (
                        (
                            scene.HtmlElement(
                                scene.HtmlTag.UL,
                                tuple(
                                    scene.HtmlElement(scene.HtmlTag.LI, (text(summary.nodes),)) for summary in summaries
                                ),
                            ),
                        )
                        if summaries is not None
                        else ()
                    )
                    if not 0 <= control.min_values <= control.max_values <= len(control.options):
                        violations.append(ConstraintViolation(part.offer.occurrence.identity, "select cardinality"))
                    texts.extend(option.label for option in control.options)
                    options = tuple(
                        scene.HtmlElement(
                            scene.HtmlTag.OPTION,
                            (scene.HtmlText(option.label),),
                            attributes=(
                                scene.HtmlAttribute(scene.HtmlAttributeName.VALUE, option.value),
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.TITLE, option.description),)
                                    if option.description is not None
                                    else ()
                                ),
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.SELECTED, value=True),)
                                    if option.default
                                    else ()
                                ),
                            ),
                        )
                        for option in control.options
                    )
                    result = (
                        *overview,
                        scene.HtmlElement(
                            scene.HtmlTag.SELECT,
                            options,
                            attributes=(
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.DISABLED, "disabled"),)
                                    if control.disabled
                                    else ()
                                ),
                                scene.HtmlAttribute(scene.HtmlAttributeName.SELECTION_MIN, control.min_values),
                                scene.HtmlAttribute(scene.HtmlAttributeName.SELECTION_MAX, control.max_values),
                            ),
                            action=scene.HtmlActionRef(control.action, control.mode)
                            if isinstance(control, scene.Select)
                            else None,
                            route=scene.HtmlRouteRef(control.route_id)
                            if isinstance(control, scene.RoutedSelect)
                            else None,
                        ),
                    )
                    result += tuple(node for child in part.children for node in visit(child))
                    return (scene.HtmlElement(scene.HtmlTag.NAV, result),) if navigation else result
                case TextSequenceRealization() as sequence:
                    if sequence.list_ordered is not None:
                        items = tuple(
                            scene.HtmlElement(
                                scene.HtmlTag.LI,
                                (text(item.nodes),),
                                attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.VALUE, item.ordinal),)
                                if sequence.list_ordered
                                else (),
                            )
                            for item in sequence.items
                        )
                        return (
                            scene.HtmlElement(scene.HtmlTag.OL if sequence.list_ordered else scene.HtmlTag.UL, items),
                            *(
                                (scene.HtmlElement(scene.HtmlTag.P, (text(sequence.notice),)),)
                                if sequence.notice
                                else ()
                            ),
                            *(node for child in part.children for node in visit(child)),
                        )
                    rendered = (
                        *tuple(
                            node
                            for index, item in enumerate(sequence.items)
                            for node in (
                                tree.literal((sequence.separator if index else sequence.prefix) + item.prefix),
                                *item.nodes,
                            )
                        ),
                        tree.literal(sequence.suffix),
                    )
                    return (
                        scene.HtmlElement(scene.HtmlTag.P, (text(rendered),)),
                        *(node for child in part.children for node in visit(child)),
                    )
                case StatusRealization() as status:
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.DIV,
                            (text(status.nodes),),
                            attributes=(
                                scene.HtmlAttribute(scene.HtmlAttributeName.ROLE, "status"),
                                scene.HtmlAttribute(scene.HtmlAttributeName.TONE, status.tone),
                                scene.HtmlAttribute(scene.HtmlAttributeName.EMPHASIS, status.emphasis),
                            ),
                            colour=scene.HtmlColourRef(status.colour) if status.colour is not None else None,
                        ),
                    )
                case ProgressRealization() as progress:
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.PROGRESS,
                            (text(progress.nodes),),
                            attributes=(
                                scene.HtmlAttribute(scene.HtmlAttributeName.VALUE, progress.value),
                                scene.HtmlAttribute(scene.HtmlAttributeName.MAX, progress.maximum),
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.ARIA_LABEL, progress.label),)
                                    if progress.label is not None
                                    else ()
                                ),
                            ),
                        ),
                    )
                case MetricRealization() as metric:
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.DL,
                            (
                                scene.HtmlElement(scene.HtmlTag.DT, (text(metric.label),)),
                                scene.HtmlElement(scene.HtmlTag.DD, (text((tree.literal(metric.value),)),)),
                            ),
                            attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.CLASS, "squid-metric"),),
                        ),
                    )
                case TimeRealization() as time:
                    prefix = (text(time.prefix),) if time.prefix else ()
                    content = scene.HtmlElement(
                        scene.HtmlTag.TIME,
                        (text((tree.literal(time.display),)),),
                        time=scene.HtmlTimeRef(time.instant, timezone=time.timezone, style=time.style),
                    )
                    return (scene.HtmlElement(scene.HtmlTag.P, (*prefix, content)),)
                case TextRealization() as payload:
                    if payload.shortening is not None and not payload.visible_nodes:
                        return ()
                    if payload.role == "code":
                        value = "".join(tree.plain(node) for node in payload.visible_nodes)
                        texts.append(value)
                        return (
                            scene.HtmlElement(
                                scene.HtmlTag.PRE, (scene.HtmlElement(scene.HtmlTag.CODE, (scene.HtmlText(value),)),)
                            ),
                        )
                    tag = (
                        scene.HtmlTag(f"h{payload.level}")
                        if payload.role == "heading"
                        else scene.HtmlTag.SMALL
                        if payload.role == "note"
                        else scene.HtmlTag.BLOCKQUOTE
                        if payload.role == "quote"
                        else scene.HtmlTag.P
                    )
                    return (scene.HtmlElement(tag, (text(payload.visible_nodes),)),)
                case TableRealization() as table:
                    context.work.consume(len(table.columns) + sum(1 + len(row.cells) for row in table.rows))
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
                case DisclosureRealization(open=open_):
                    children = tuple(node for child in part.children for node in visit(child))
                    toggle = children[0]
                    if not isinstance(toggle, scene.HtmlElement) or toggle.action is None:
                        message = "disclosure needs its toggle control"
                        raise DrawInvariantError(message)
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.DETAILS,
                            (replace(toggle, tag=scene.HtmlTag.SUMMARY, action=None), *children[1:]),
                            attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.OPEN, value=True),)
                            if open_
                            else (),
                            action=toggle.action,
                        ),
                    )
                case ActionsRealization(navigation=True):
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.NAV, tuple(node for child in part.children for node in visit(child))
                        ),
                    )
                case FieldsRealization(notice=notice):
                    return (
                        *(node for child in part.children for node in visit(child)),
                        *((scene.HtmlElement(scene.HtmlTag.P, (text(notice),)),) if notice else ()),
                    )
                case FlowRealization() | RowRealization() | ActionsRealization():
                    return tuple(node for child in part.children for node in visit(child))
                case PanelRealization() as panel:
                    children = tuple(node for child in part.children for node in visit(child))
                    if panel.thumbnail is not None:
                        thumbnail = scene.HtmlElement(
                            scene.HtmlTag.IMG,
                            (),
                            attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.ALT, panel.heading),),
                            url=scene.HtmlUrlRef(panel.thumbnail),
                        )
                        children = (*children[:1], thumbnail, *children[1:])
                    colour = scene.HtmlColourRef(panel.accent) if panel.accent is not None else None
                    tag = scene.HtmlTag.DIV if panel.kind == "block" else scene.HtmlTag(panel.kind)
                    attributes = (
                        (
                            scene.HtmlAttribute(scene.HtmlAttributeName.CLASS, "squid-aside"),
                            scene.HtmlAttribute(scene.HtmlAttributeName.TONE, panel.tone),
                        )
                        if panel.tone is not None
                        else ()
                    )
                    return (scene.HtmlElement(tag, children, colour=colour, attributes=attributes),)
                case ImageRealization() as media:
                    asset = media.source if isinstance(media.source, Asset) else None
                    if asset is not None:
                        uses.append(SharedUse(ResourceIdentity("attachment", asset.key), asset, EMPTY_COST))
                    return (
                        scene.HtmlElement(
                            scene.HtmlTag.IMG,
                            (),
                            attributes=(
                                scene.HtmlAttribute(scene.HtmlAttributeName.ALT, media.description),
                                *(
                                    (scene.HtmlAttribute(scene.HtmlAttributeName.CLASS, "squid-spoiler"),)
                                    if media.spoiler
                                    else ()
                                ),
                            ),
                            url=scene.HtmlUrlRef(media.source) if isinstance(media.source, str) else None,
                            asset=scene.HtmlAssetRef(asset.key, asset.name, asset.media_type)
                            if asset is not None
                            else None,
                        ),
                    )
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
                    violations.append(UnsupportedCapability(part.offer.occurrence.identity, type(unknown).__name__))
                    return ()

        body = scene.HtmlBody(visit(candidate), locale=context.localization.locale)
        return Evaluation(
            additive_cost=scopes[candidate.offer.occurrence.identity],
            scoped_costs=scopes,
            resource_axes=frozenset({Axis.DISPLAY_TEXT}),
            resources=SharedResources.combine(uses),
            violations=tuple(violations),
            effects=tuple(effects),
            interactions=frozenset(interactions),
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
        assets: set[ResourceIdentity] = {ResourceIdentity("attachment", asset.key) for asset in context.assets}

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
            or expected.cost != ResourceCost({Axis.DISPLAY_TEXT: sum(map(len, texts))})
        ):
            message = "built HTML composition disagrees with evaluation"
            raise DrawInvariantError(message)

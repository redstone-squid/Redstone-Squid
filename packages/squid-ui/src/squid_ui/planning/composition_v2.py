"""Direct Components V2 rules for the shared composition boundary."""

from dataclasses import dataclass
from typing import assert_never

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
    ResourceIdentity,
    SemanticOccurrence,
    SharedResources,
    SharedUse,
    UnsupportedCapability,
)
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.realizations import (
    ControlRealization,
    DownloadRealization,
    FlowRealization,
    PanelRealization,
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
from squid_ui.text import Markup


def _select_violations(node: scene.Select | scene.RoutedSelect, limits: V2Limits) -> tuple[str, ...]:
    errors: list[str] = []
    component = limits.components
    if not 1 <= len(node.options) <= component.select_options:
        errors.append("select option count")
    if not 0 <= node.min_values <= node.max_values <= len(node.options):
        errors.append("select cardinality")
    if len(node.placeholder or "") > component.select_placeholder:
        errors.append("select placeholder")
    identifier = node.action if isinstance(node, scene.Select) else node.route_id
    if len(identifier) > component.custom_id:
        errors.append("select identifier")
    for option in node.options:
        if not option.label or len(option.label) > component.option_label:
            errors.append("select option label")
        if not option.value or len(option.value) > component.option_value:
            errors.append("select option value")
        if len(option.description or "") > component.option_description:
            errors.append("select option description")
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class V2CompositionRules[RenderTargetT, ContextT]:
    limits: V2Limits = V2Limits()

    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]:
        return semantic_offers(
            occurrence, context, table_forms=(TableForm.RECORDS,), panels=True, character_budgets=True
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
                    if not control.label:
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
                case RowRealization():
                    components += 1
                    if len(part.children) > self.limits.components.row_buttons:
                        violations.append(
                            LocalCapacityExceeded(
                                part.offer.occurrence.identity,
                                "row.buttons",
                                len(part.children),
                                self.limits.components.row_buttons,
                            )
                        )
                    items: list[scene.Button | scene.RoutedButton | scene.Link] = []
                    for child in part.children:
                        for item in visit(child, in_panel=in_panel, in_row=True):
                            if not isinstance(item, scene.Button | scene.RoutedButton | scene.Link):
                                message = "navigation row contains a non-button"
                                raise DrawInvariantError(message)
                            items.append(item)
                    return (scene.Row(tuple(items)),)
                case SelectRealization(node=node):
                    components += 2
                    violations.extend(
                        ConstraintViolation(part.offer.occurrence.identity, detail)
                        for detail in _select_violations(node, self.limits)
                    )
                    return (node,)
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
                            for index, item in enumerate(sequence.items)
                        )
                        + sequence.suffix
                    )
                    return (
                        *rendered_text(rendered),
                        *(node for child in part.children for node in visit(child, in_panel=in_panel)),
                    )
                case TextRealization(nodes=nodes):
                    return text(nodes)
                case TableRealization() as table:
                    context.work.consume(len(table.columns) + sum(1 + len(row.cells) for row in table.rows))
                    if table.form != TableForm.RECORDS:
                        violations.append(UnsupportedCapability(part.offer.occurrence.identity, "native table"))
                    return tuple(node for row in record_text(table) for node in text(row))
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
                case PanelRealization():
                    components += 1
                    if in_panel:
                        violations.append(ConstraintViolation(part.offer.occurrence.identity, "nested V2 container"))
                    children = tuple(node for child in part.children for node in visit(child, in_panel=True))
                    return (scene.Panel(children, part.offer.payload.accent),)
                case FlowRealization():
                    return tuple(node for child in part.children for node in visit(child, in_panel=in_panel))
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
                    if not node.label or len(node.label) > self.limits.components.button_label:
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
                case scene.Select() | scene.RoutedSelect():
                    components += 1  # Selects occupy an implicit action row.
                    if _select_violations(node, self.limits):
                        msg_0 = "built V2 select violates its limits"
                        raise DrawInvariantError(msg_0)
                case scene.Text(content=content):
                    texts.append(content)
                case scene.File():
                    assets.add(node.asset_key)
                    use = expected.resources.uses.get(ResourceIdentity("attachment", node.asset_key))
                    if use is None or (node.name, node.media_type) != (use.value.name, use.value.media_type):
                        message = "built file disagrees with its declared shared asset"
                        raise DrawInvariantError(message)
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

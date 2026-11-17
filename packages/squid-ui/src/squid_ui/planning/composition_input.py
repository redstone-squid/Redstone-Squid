"""Prepare portable authoring nodes, delegating native vocabulary to the target."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from squid_ui import scene
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.document import Document
from squid_ui.authoring.presentations import Alternatives, Budget, TextPresentation
from squid_ui.content.text import Localization, resolve_text
from squid_ui.native import NativeNode, NativeTraits
from squid_ui.planning.adapter import ExtensionAdapter
from squid_ui.planning.dialect import InputPolicy, NativeInput
from squid_ui.planning.layout_structure import LAYOUT_CONTAINERS, layout_parts
from squid_ui.planning.pagination.content import RootRegion
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantic_index import UnsupportedSemanticError, semantic_graph
from squid_ui.target_types import Renderable


@dataclass(frozen=True, slots=True)
class PreparedComposition[RenderTargetT]:
    document: Document[RenderTargetT]
    limitation: str | None = None
    combinations: int = 1


@dataclass
class InputPreparation:
    """One traversal's shared constraints and search-size estimate.

    Native visitors call visit for their children, so portable wrappers, alternative
    selection, and work estimates have the same owner in every target.
    """

    policy: InputPolicy
    localization: Localization
    capabilities: frozenset[str]
    extensions: Mapping[str, ExtensionAdapter[Any, Any]]
    combinations: int = 1
    text_choices: int = 0
    shortening_depth: int = 0
    variant_index: int = 0
    native: NativeInput | None = field(init=False)

    def __post_init__(self) -> None:
        self.native = self.policy.native(self) if self.policy.native is not None else None

    def visit(self, node: Renderable[Any]) -> Renderable[Any]:
        match node:
            case sem.Region() | sem.Paged() | sem.List() | sem.Navigation() | sem.Grid():
                if isinstance(node, sem.Paged):
                    if not self.policy.character_axis:
                        msg = "Paged requires one shared character axis; use regions with this target's resource limits"
                        raise UnsupportedSemanticError(msg)
                    return replace(node, node=self.visit(node.node))
                if isinstance(node, sem.Region):
                    parts = layout_parts(node)
                    return replace(
                        node,
                        children=tuple(map(self.visit, parts.children)),
                        header=tuple(map(self.visit, parts.header)),
                        footer=tuple(map(self.visit, parts.footer)),
                    )
                if isinstance(node, sem.Grid):
                    self.combinations = max(self.combinations, len(node.cells))
                if isinstance(node, sem.List) and node.page_size is not None:
                    self.combinations *= max(1, (len(node.items) + node.page_size - 1) // node.page_size)
                return node
            case sem.Roster():
                self.combinations = max(
                    self.combinations,
                    sum(len(group.members) for group in node.placement.groups) + len(node.placement.waitlist),
                )
                return node
            case sem.Items():
                return replace(
                    node,
                    items=tuple(replace(item, children=tuple(map(self.visit, item.children))) for item in node.items),
                )
            case sem.Details():
                return replace(node, children=tuple(map(self.visit, node.children)))
            case _ if isinstance(node, LAYOUT_CONTAINERS):
                return replace(node, children=tuple(map(self.visit, layout_parts(node).children)))
            case sem.OptionalContent():
                return replace(node, node=self.visit(node.node))
            case sem.Truncated() | sem.Spilled() | sem.BestEffort():
                self.shortening_depth += 1
                try:
                    return replace(node, node=self.visit(node.node))
                finally:
                    self.shortening_depth -= 1
            case sem.Fields():
                return node
            case sem.Unbreakable() | sem.KeepWithNext() | sem.Budgeted() | Budget():
                if self.policy.budget_ladder:
                    if isinstance(node, sem.Budgeted):
                        self.combinations = max(self.combinations, node.preferred)
                    elif isinstance(node, Budget):
                        self.combinations = max(self.combinations, node.limits.get(Axis.DISPLAY_TEXT, 1))
                return replace(node, node=self.visit(node.node))
            case (
                sem.Heading()
                | sem.Note()
                | sem.Code()
                | sem.Quote()
                | sem.Status()
                | sem.ProgressBar()
                | sem.Metric()
                | sem.Timestamp()
                | sem.ZonedTimestamp()
                | sem.Media()
                | sem.Figure()
                | sem.Paragraph()
                | sem.Table()
                | sem.Download()
                | sem.ActionControl()
                | sem.RoutedActionControl()
                | sem.Link()
                | sem.FormTrigger()
                | sem.Toggle()
                | sem.Choices()
                | sem.Entities()
                | sem.RoutedChoices()
                | sem.ControlGroup()
                | TextPresentation()
            ):
                if self.shortening_depth and isinstance(
                    node, sem.Paragraph | sem.Heading | sem.Note | sem.Code | sem.Quote
                ):
                    length = len(resolve_text(node.content, self.localization).plain())
                    self.text_choices += min(self.policy.text_ladder, length)
                    self.combinations = max(self.combinations, self.text_choices)
                return node
            case sem.ActionControls():
                self.combinations = max(self.combinations, len(node.items) * 16)
                return node
            case sem.FallbackContent():
                return replace(
                    node, primary=self.visit(node.primary), alternates=tuple(map(self.visit, node.alternates))
                )
            case NativeNode() if self.native is None and (fallback := NativeTraits.of(node).fallback) is not None:
                self.visit(fallback)
                return node
            case NativeNode() if (group := node.semantics.alternatives(node)) is not None:
                self.variant_index += 1
                # Conditional trees charge assignment copying and independent sibling probes.
                self.combinations = max(self.combinations, 8 * self.variant_index * self.variant_index)
                return self.visit(replace(group, key=NativeTraits.of(node).key or f"@variants-{self.variant_index}"))
            case Alternatives():
                # Canonical content describes identity; it need not render on this target.
                semantic_graph(node.content)
                choices = []
                limitations = []
                for choice in node.choices:
                    if missing := choice.requires - self.capabilities:
                        limitations.append("missing capabilities: " + ", ".join(sorted(missing)))
                        continue
                    try:
                        choices.append(replace(choice, node=self.visit(choice.node)))
                    except UnsupportedSemanticError as error:
                        limitations.append(error.kind)
                if not choices:
                    msg = "no checked presentation supports this target: " + "; ".join(dict.fromkeys(limitations))
                    raise UnsupportedSemanticError(msg)
                return replace(node, choices=tuple(choices))
            case _:
                if self.native is not None and (prepared := self.native.visit(node)) is not None:
                    return prepared
                raise UnsupportedSemanticError(type(node).__name__)


def prepare_composition[BodyT: scene.Body, RenderTargetT, AdapterT](
    document: Document[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
) -> PreparedComposition[RenderTargetT]:
    policy = request.target.dialect.input_policy(request.target.limits)
    if not policy.inspect:
        return PreparedComposition(document)
    preparation = InputPreparation(policy, request.localization, request.target.capabilities, request.target.extensions)
    try:
        children = tuple(map(preparation.visit, document.children))
        if document.key and request.nav is not None:
            children = (RootRegion[RenderTargetT](children, key=document.key, title=""),)
        return PreparedComposition(replace(document, children=children), combinations=preparation.combinations)
    except UnsupportedSemanticError as error:
        return PreparedComposition(document, limitation=error.kind)

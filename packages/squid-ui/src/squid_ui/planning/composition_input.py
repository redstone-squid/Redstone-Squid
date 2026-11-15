"""Validate authored layout vocabulary and prepare it for shared composition."""

from dataclasses import dataclass, replace

from squid_ui import scene
from squid_ui import semantic as sem
from squid_ui.document import Document
from squid_ui.planning.native_extensions import ComposableExtension
from squid_ui.planning.pagination_content import RootRegion
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import UnsupportedSemanticError, semantic_graph, variant_alternatives
from squid_ui.presentations import Alternatives, Budget, TextPresentation
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import Alt, Alts, Condense, Drop, Never, Paginate, Spill, Truncate
from squid_ui.target_types import Renderable
from squid_ui.text import resolve_text


@dataclass(frozen=True, slots=True)
class PreparedComposition[RenderTargetT]:
    document: Document[RenderTargetT]
    limitation: str | None = None
    combinations: int = 1


def prepare_composition[BodyT: scene.Body, RenderTargetT, AdapterT](
    document: Document[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
) -> PreparedComposition[RenderTargetT]:
    policy = request.target.dialect.input_policy(request.target.limits)
    if not policy.inspect:
        return PreparedComposition(document)
    combinations = 1
    text_choices = 0
    shortening_depth = 0
    card_depth = 0
    variant_index = 0
    text_ladder = policy.text_ladder

    def visit[NodeTargetT](node: Renderable[NodeTargetT]) -> Renderable[NodeTargetT]:
        nonlocal combinations, shortening_depth, text_choices, card_depth, variant_index
        match node:
            case sem.Region() | sem.Paged() | sem.List() | sem.Navigation() | sem.Grid():
                if isinstance(node, sem.Paged):
                    if not policy.character_axis:
                        msg = "Paged requires one shared character axis; use regions with this target's resource limits"
                        raise UnsupportedSemanticError(msg)
                    return replace(node, node=visit(node.node))
                if isinstance(node, sem.Region):
                    return replace(
                        node,
                        children=tuple(map(visit, node.children)),
                        header=tuple(map(visit, node.header)),
                        footer=tuple(map(visit, node.footer)),
                    )
                if isinstance(node, sem.Grid):
                    combinations = max(combinations, len(node.cells))
                if isinstance(node, sem.List) and node.page_size is not None:
                    combinations *= max(1, (len(node.items) + node.page_size - 1) // node.page_size)
                return node
            case sem.Roster():
                combinations = max(
                    combinations,
                    sum(len(group.members) for group in node.placement.groups) + len(node.placement.waitlist),
                )
                return node
            case sem.Items():
                return replace(
                    node, items=tuple(replace(item, children=tuple(map(visit, item.children))) for item in node.items)
                )
            case sem.Details():
                return replace(node, children=tuple(map(visit, node.children)))
            case (
                sem.Group()
                | sem.Stack()
                | sem.Block()
                | sem.Cluster()
                | sem.Section()
                | sem.Article()
                | sem.Aside()
                | sem.Themed()
            ):
                return replace(node, children=tuple(map(visit, node.children)))
            case sem.OptionalContent():
                return replace(node, node=visit(node.node))
            case sem.Truncated() | sem.Spilled() | sem.BestEffort():
                shortening_depth += 1
                try:
                    return replace(node, node=visit(node.node))
                finally:
                    shortening_depth -= 1
            case sem.Fields():
                return node
            case (
                primitive.Panel()
                | primitive.Section()
                | primitive.Thumbnail()
                | primitive.Sep()
                | primitive.Gallery()
                | primitive.MediaCollection()
                | primitive.File()
            ):
                if primitive.Panel not in policy.native_types:
                    message = "native Components V2 layout on another target"
                    raise UnsupportedSemanticError(message)
                if isinstance(node, primitive.Panel):
                    return replace(node, children=tuple(map(visit, node.children)))
                if isinstance(node, primitive.Section):
                    if any(isinstance(text.overflow, Paginate) for text in node.texts):
                        message = "pagination cannot be nested in a Section; paginate its enclosing region"
                        raise UnsupportedSemanticError(message)
                    return replace(node, texts=tuple(map(visit, node.texts)), accessory=visit(node.accessory))
                return node
            case primitive.Content():
                if primitive.Card not in policy.native_types:
                    msg = "native classic message content on another target"
                    raise UnsupportedSemanticError(msg)
                visit(primitive.Text(node.content, overflow=node.overflow, priority=node.priority))
                return node
            case primitive.Card():
                if primitive.Card not in policy.native_types:
                    msg = "native classic embed on another target"
                    raise UnsupportedSemanticError(msg)
                card_depth += 1
                try:
                    for child in node.children:
                        visit(child)
                    slots = [entry for field in node.fields for entry in (field.name, field.value)]
                    if node.title is not None:
                        slots.append(node.title)
                    if node.footer is not None:
                        slots.append(node.footer.text)
                    if node.author is not None:
                        slots.append(node.author.name)
                    for slot in slots:
                        visit(primitive.card_text(slot))
                finally:
                    card_depth -= 1
                return node
            case primitive.Budget() | primitive.Break():
                if primitive.Text not in policy.native_types:
                    msg = "Discord native layout policy on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                if isinstance(node, primitive.Budget) and primitive.Panel not in policy.native_types:
                    msg = "Budget requires one shared character axis; use resource budgets on this target"
                    raise UnsupportedSemanticError(msg)
                for child in node.children:
                    visit(child)
                return node
            case primitive.Time() | primitive.ZonedTime():
                if primitive.Text not in policy.native_types:
                    msg = "Discord native time on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.Extension():
                adapter = request.target.extensions.get(node.kind.name)
                if adapter is None or primitive.Panel not in policy.native_types:
                    visit(node.fallback)
                elif not isinstance(adapter, ComposableExtension):
                    msg = f"extension {node.kind.name!r} needs a compose() contract for shared allocation"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.RawItem():
                if primitive.Panel not in policy.native_types or node.composition is None:
                    msg = "RawItem requires a native Components V2 composition contract"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.Row() | primitive.ControlGroup():
                if primitive.Text not in policy.native_types:
                    msg = "Discord native controls on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                for item in node.items:
                    if isinstance(item, primitive.RawItem):
                        visit(item)
                        continue
                    if not isinstance(
                        item, primitive.Button | primitive.RoutedButton | primitive.LinkButton | primitive.PremiumButton
                    ):
                        raise UnsupportedSemanticError(type(item).__name__)
                return node
            case (
                primitive.Button()
                | primitive.RoutedButton()
                | primitive.LinkButton()
                | primitive.PremiumButton()
                | primitive.SelectMenu()
                | primitive.RoutedSelect()
                | primitive.EntitySelect()
            ):
                if primitive.Text not in policy.native_types:
                    msg = "Discord native controls on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                return node
            case sem.Unbreakable() | sem.KeepWithNext() | sem.Budgeted() | Budget():
                if policy.budget_ladder:
                    if isinstance(node, sem.Budgeted):
                        combinations = max(combinations, node.preferred)
                    elif isinstance(node, Budget):
                        combinations = max(combinations, node.limits.get(Axis.DISPLAY_TEXT, 1))
                return replace(node, node=visit(node.node))
            case primitive.Text() | primitive.Code() | primitive.Lines() | primitive.Heading() | primitive.Footer():
                if primitive.Text not in policy.native_types:
                    msg = "Discord text primitives on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                overflow = node.overflow
                if card_depth and isinstance(overflow, Paginate):
                    msg = "embed text cannot contain pager controls; paginate the enclosing cards"
                    raise UnsupportedSemanticError(msg)
                if isinstance(node, primitive.Lines) and isinstance(
                    overflow, Never | Truncate | Spill | Drop | Condense | Alts
                ):
                    steps = sum(len(item.fallbacks) if isinstance(item, Alt) else 0 for item in node.lines)
                    combinations = max(combinations, len(node.lines) + steps)
                    if isinstance(overflow, Alts):
                        text_choices += min(text_ladder, len(overflow.ladder[-1]))
                        combinations = max(combinations, text_choices + len(overflow.ladder))
                    return node
                if isinstance(overflow, Never | Truncate | Alts | Drop | Spill | Condense) and not isinstance(
                    node, primitive.Lines
                ):
                    if isinstance(overflow, Truncate | Alts | Condense) or shortening_depth:
                        length = len(resolve_text(node.content, request.localization).plain())
                        if isinstance(overflow, Alts):
                            length = max(length, len(overflow.ladder[-1]))
                        text_choices += min(text_ladder, length)
                        combinations = max(combinations, text_choices)
                    return node
                if not isinstance(overflow, Paginate):
                    msg = "primitive text overflow alternatives"
                    raise UnsupportedSemanticError(msg)
                if overflow.min_fill and primitive.Panel not in policy.native_types:
                    msg = "primitive min_fill requires one shared character axis"
                    raise UnsupportedSemanticError(msg)
                if isinstance(node, primitive.Lines):
                    steps = sum(len(item.fallbacks) if isinstance(item, Alt) else 0 for item in node.lines)
                    combinations = max(combinations, len(node.lines) + steps)
                    if overflow.per is not None:
                        combinations *= max(1, (len(node.lines) + overflow.per - 1) // overflow.per)
                    return node
                content = resolve_text(node.content, request.localization).plain()
                count = len(content.split(overflow.boundary)) if overflow.boundary else 1
                combinations = max(combinations, count * 16)
                if overflow.per is not None:
                    combinations *= max(1, (count + overflow.per - 1) // overflow.per)
                return node
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
                if shortening_depth and isinstance(node, sem.Paragraph | sem.Heading | sem.Note | sem.Code | sem.Quote):
                    length = len(resolve_text(node.content, request.localization).plain())
                    text_choices += min(text_ladder, length)
                    combinations = max(combinations, text_choices)
                return node
            case sem.ActionControls():
                combinations = max(combinations, len(node.items) * 16)
                return node
            case sem.FallbackContent():
                return replace(node, primary=visit(node.primary), alternates=tuple(map(visit, node.alternates)))
            case primitive.Variants():
                variant_index += 1
                # Conditional trees charge assignment copying and independent sibling probes.
                combinations = max(combinations, 8 * variant_index * variant_index)
                group = variant_alternatives(node)
                return visit(replace(group, key=node.key or f"@variants-{variant_index}"))
            case Alternatives():
                # Canonical content describes identity; it need not render on this target.
                semantic_graph(node.content)
                choices = []
                limitations = []
                for choice in node.choices:
                    if missing := choice.requires - request.target.capabilities:
                        limitations.append("missing capabilities: " + ", ".join(sorted(missing)))
                        continue
                    try:
                        choices.append(replace(choice, node=visit(choice.node)))
                    except UnsupportedSemanticError as error:
                        limitations.append(error.kind)
                if not choices:
                    msg = "no checked presentation supports this target: " + "; ".join(dict.fromkeys(limitations))
                    raise UnsupportedSemanticError(msg)
                return replace(node, choices=tuple(choices))
            case _:
                raise UnsupportedSemanticError(type(node).__name__)

    try:
        children = tuple(map(visit, document.children))
        if document.key and request.nav is not None:
            children = (RootRegion[RenderTargetT](children, key=document.key, title=""),)
        prepared = replace(document, children=children)
        return PreparedComposition(prepared, combinations=combinations)
    except UnsupportedSemanticError as error:
        return PreparedComposition(document, limitation=error.kind)

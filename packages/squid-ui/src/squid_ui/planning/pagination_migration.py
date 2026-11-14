"""Translate supported pagination declarations before selecting a production planner.

Compatibility routes are chosen from vocabulary and target capabilities. Search
failure never causes a second attempt with a different solver.
"""

from dataclasses import dataclass, replace

from squid_ui import scene
from squid_ui import semantic as sem
from squid_ui.document import Document
from squid_ui.html.target import HtmlLimits
from squid_ui.planning.limits import ClassicLimits, V2Limits
from squid_ui.planning.pagination_content import RootRegion, TextPages
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.semantics import UnsupportedSemanticError
from squid_ui.presentations import Alternatives, Budget, TextPresentation
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import Alt, Paginate
from squid_ui.slack.target import SlackHomeLimits, SlackMessageLimits, SlackModalLimits
from squid_ui.target_types import Renderable


@dataclass(frozen=True, slots=True)
class PaginationMigration[RenderTargetT]:
    document: Document[RenderTargetT]
    enabled: bool
    limitation: str | None = None
    combinations: int = 1


def prepare_pagination[BodyT: scene.Body, RenderTargetT, AdapterT](
    document: Document[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
) -> PaginationMigration[RenderTargetT]:
    limits = request.target.limits
    if isinstance(limits, SlackModalLimits):
        return PaginationMigration(
            document, enabled=False, limitation="Slack modals require one top-level form trigger"
        )
    if not isinstance(limits, ClassicLimits | V2Limits | SlackMessageLimits | SlackHomeLimits | HtmlLimits):
        return PaginationMigration(document, enabled=False, limitation="target has no shared composition rules")
    pageable = False
    combinations = 1

    def visit(node: Renderable[RenderTargetT]) -> Renderable[RenderTargetT]:
        nonlocal pageable, combinations
        match node:
            case sem.Region() | sem.Paged() | sem.List() | sem.Navigation():
                pageable = True
                if isinstance(node, sem.Paged):
                    if not isinstance(limits, V2Limits):
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
                if isinstance(node, sem.List) and node.page_size is not None:
                    combinations *= max(1, (len(node.items) + node.page_size - 1) // node.page_size)
                return node
            case sem.Items():
                pageable = True
                return replace(
                    node, items=tuple(replace(item, children=tuple(map(visit, item.children))) for item in node.items)
                )
            case sem.Details():
                pageable = True
                return replace(node, children=tuple(map(visit, node.children)))
            case sem.Group() | sem.Stack() | sem.Block() | sem.Cluster() | sem.Section() | sem.Article() | sem.Themed():
                return replace(node, children=tuple(map(visit, node.children)))
            case sem.Unbreakable() | sem.KeepWithNext() | sem.Budgeted() | Budget():
                return replace(node, node=visit(node.node))
            case primitive.Text() | primitive.Code() | primitive.Lines() | primitive.Heading() | primitive.Footer():
                if not isinstance(limits, V2Limits | ClassicLimits):
                    msg = "Discord text primitives on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                overflow = node.overflow
                if not isinstance(overflow, Paginate):
                    msg = "primitive text overflow alternatives"
                    raise UnsupportedSemanticError(msg)
                if overflow.min_fill and not isinstance(limits, V2Limits):
                    msg = "primitive min_fill requires one shared character axis"
                    raise UnsupportedSemanticError(msg)
                if overflow.key is None:
                    msg = "unkeyed primitive pagination"
                    raise UnsupportedSemanticError(msg)
                pageable = True
                if isinstance(node, primitive.Lines):
                    if any(isinstance(item, Alt) for item in node.lines):
                        msg = "line degradation alternatives"
                        raise UnsupportedSemanticError(msg)
                    items = tuple(
                        sem.ListItem(node.keys[index] if node.keys else f"@{index}", item)
                        for index, item in enumerate(node.lines)
                        if not isinstance(item, Alt)
                    )
                    separator = node.join
                else:
                    if not isinstance(node.content, str):
                        msg = "rich text fragmentation"
                        raise UnsupportedSemanticError(msg)
                    content = (
                        node.content.replace("```", "``\u200b`") if isinstance(node, primitive.Code) else node.content
                    )
                    separator = overflow.boundary
                    parts = content.split(separator) if separator else [content]
                    items = tuple(sem.ListItem(f"@{index}", part) for index, part in enumerate(parts))
                if overflow.per is not None:
                    combinations *= max(1, (len(items) + overflow.per - 1) // overflow.per)
                prefix = (
                    f"```{node.lang}\n"
                    if isinstance(node, primitive.Code)
                    else "#" * node.level + " "
                    if isinstance(node, primitive.Heading)
                    else "-# "
                    if isinstance(node, primitive.Footer)
                    else ""
                )
                suffix = "\n```" if isinstance(node, primitive.Code) else ""
                return TextPages[RenderTargetT](
                    items,
                    overflow.key,
                    overflow.page,
                    separator,
                    prefix,
                    suffix,
                    overflow.per,
                    overflow.min_fill,
                    overflow.widows,
                    overflow.footer,
                    literal=isinstance(node, primitive.Code),
                )
            case (
                sem.Heading()
                | sem.Note()
                | sem.Code()
                | sem.Quote()
                | sem.Status()
                | sem.ProgressBar()
                | sem.Fields()
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
                return node
            case sem.ActionControls():
                return node
            case sem.FallbackContent():
                msg = "FallbackContent has no declared source correspondence; use alternatives"
                raise UnsupportedSemanticError(msg)
            case Alternatives():
                return replace(
                    node,
                    content=visit(node.content),
                    choices=tuple(replace(choice, node=visit(choice.node)) for choice in node.choices),
                )
            case _:
                raise UnsupportedSemanticError(type(node).__name__)

    try:
        children = tuple(map(visit, document.children))
        if document.key and request.nav is not None and not pageable:
            children = (RootRegion[RenderTargetT](children, key=document.key, title=""),)
            pageable = True
        prepared = replace(document, children=children)
        return PaginationMigration(prepared, pageable, combinations=combinations)
    except UnsupportedSemanticError as error:
        return PaginationMigration(document, enabled=False, limitation=error.kind)

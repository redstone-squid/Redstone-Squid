"""Compare allocation inputs while keeping controlled coordinates out of layout identity.

This comparison follows supported semantic node types. Callbacks, resources,
policies, and container structure still participate; only Controlled.value is
ignored. Unknown extensions prevent reuse until they supply an explicit contract.
"""

from dataclasses import replace

from squid_ui import semantic as sem
from squid_ui.document import Document
from squid_ui.ownership import Controlled
from squid_ui.planning.pagination_content import TextPages
from squid_ui.presentations import Alternatives, Budget, TextPresentation
from squid_ui.primitives.constraints import PageOwnership
from squid_ui.target_types import Renderable


def _page_configuration(owner: PageOwnership) -> PageOwnership:
    return replace(owner, value=0) if isinstance(owner, Controlled) else owner


def same_allocation_document[RenderTargetT, ContextT](
    left: Document[RenderTargetT, ContextT], right: Document[RenderTargetT, ContextT]
) -> bool:
    return left.key == right.key and left.assets == right.assets and _same_children(left.children, right.children)


def _same_children[RenderTargetT, ContextT](
    left: tuple[Renderable[RenderTargetT, ContextT], ...], right: tuple[Renderable[RenderTargetT, ContextT], ...]
) -> bool:
    return len(left) == len(right) and all(_same_node(a, b) for a, b in zip(left, right, strict=True))


def _same_node[RenderTargetT, ContextT](
    left: Renderable[RenderTargetT, ContextT], right: Renderable[RenderTargetT, ContextT]
) -> bool:
    if type(left) is not type(right):
        return False
    match left, right:
        case sem.Region(), sem.Region():
            return (
                replace(left, children=(), header=(), footer=(), page=_page_configuration(left.page))
                == replace(right, children=(), header=(), footer=(), page=_page_configuration(right.page))
                and _same_children(left.children, right.children)
                and _same_children(left.header, right.header)
                and _same_children(left.footer, right.footer)
            )
        case sem.Paged(), sem.Paged():
            return replace(left, node=sem.Group(()), page=_page_configuration(left.page)) == replace(
                right, node=sem.Group(()), page=_page_configuration(right.page)
            ) and _same_node(left.node, right.node)
        case ((sem.List() | TextPages()), (sem.List() | TextPages())):
            return replace(left, page=_page_configuration(left.page)) == replace(
                right, page=_page_configuration(right.page)
            )
        case ((sem.Group() | sem.Stack() | sem.Block()), (sem.Group() | sem.Stack() | sem.Block())):
            return replace(left, children=()) == replace(right, children=()) and _same_children(
                left.children, right.children
            )
        case (
            (sem.Unbreakable() | sem.KeepWithNext() | sem.Budgeted() | Budget()),
            (sem.Unbreakable() | sem.KeepWithNext() | sem.Budgeted() | Budget()),
        ):
            return replace(left, node=sem.Group(())) == replace(right, node=sem.Group(())) and _same_node(
                left.node, right.node
            )
        case Alternatives(), Alternatives():
            return (
                left.key == right.key
                and _same_node(left.content, right.content)
                and len(left.choices) == len(right.choices)
                and all(
                    replace(a, node=sem.Group(())) == replace(b, node=sem.Group(())) and _same_node(a.node, b.node)
                    for a, b in zip(left.choices, right.choices, strict=True)
                )
            )
        case (
            sem.Paragraph()
            | sem.Table()
            | sem.Download()
            | sem.ActionControl()
            | sem.RoutedActionControl()
            | sem.Link()
            | sem.FormTrigger()
            | sem.ActionControls()
            | sem.ControlGroup()
            | TextPresentation(),
            _,
        ):
            return left == right
        case _:
            return False

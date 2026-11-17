"""Compare allocation inputs while keeping controlled coordinates out of layout identity.

This comparison follows supported semantic node types. Resources,
policies, and container structure still participate; only Controlled.value is
ignored along with ActionControl.on_trigger. Current action handlers must be rebound
when presenting a reused allocation. Unknown extensions prevent reuse until they supply an explicit contract.
"""

from dataclasses import replace
from typing import Any

from squid_ui._presentation import ValueBinding
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.document import Document
from squid_ui.authoring.presentations import Alternatives, Budget, TextPresentation
from squid_ui.interactions.ownership import Controlled
from squid_ui.native import NativeNode
from squid_ui.overflow import PageOwnership
from squid_ui.target_types import Renderable

type AllocationNode = (
    Renderable[Any, Any]
    | sem.ControlGroup[Any]
    | sem.ActionControl[Any]
    | sem.RoutedActionControl
    | sem.Link
    | sem.Toggle[Any]
    | sem.FormTrigger[Any]
)


def same_allocation_document[RenderTargetT, ContextT](
    left: Document[RenderTargetT, ContextT], right: Document[RenderTargetT, ContextT]
) -> bool:
    return (
        left.key == right.key
        and left.assets == right.assets
        and _AllocationComparison().same_children(left.children, right.children)
    )


class _AllocationComparison:
    def __init__(self, *, rebind_actions: bool = True) -> None:
        self.rebind_actions = rebind_actions

    @staticmethod
    def _page_configuration(owner: PageOwnership) -> PageOwnership:
        return replace(owner, value=0) if isinstance(owner, Controlled) else owner

    def same_children(
        self,
        left: tuple[AllocationNode, ...],
        right: tuple[AllocationNode, ...],
    ) -> bool:
        return len(left) == len(right) and all(self.same_node(a, b) for a, b in zip(left, right, strict=True))

    def same_node(self, left: AllocationNode, right: AllocationNode) -> bool:
        if type(left) is not type(right):
            return False
        match left, right:
            case sem.Region(), sem.Region():
                return (
                    replace(left, children=(), header=(), footer=(), page=self._page_configuration(left.page))
                    == replace(right, children=(), header=(), footer=(), page=self._page_configuration(right.page))
                    and self.same_children(left.children, right.children)
                    and self.same_children(left.header, right.header)
                    and self.same_children(left.footer, right.footer)
                )
            case sem.Paged(), sem.Paged():
                return replace(left, node=sem.Group(()), page=self._page_configuration(left.page)) == replace(
                    right, node=sem.Group(()), page=self._page_configuration(right.page)
                ) and self.same_node(left.node, right.node)
            case ((sem.List()), (sem.List())):
                return replace(left, page=self._page_configuration(left.page)) == replace(
                    right, page=self._page_configuration(right.page)
                )
            case (
                (
                    sem.Group()
                    | sem.Stack()
                    | sem.Block()
                    | sem.Section()
                    | sem.Article()
                    | sem.Aside()
                    | sem.Cluster()
                    | sem.Themed()
                ),
                (
                    sem.Group()
                    | sem.Stack()
                    | sem.Block()
                    | sem.Section()
                    | sem.Article()
                    | sem.Aside()
                    | sem.Cluster()
                    | sem.Themed()
                ),
            ):
                return replace(left, children=()) == replace(right, children=()) and self.same_children(
                    left.children, right.children
                )
            case (
                (
                    sem.Unbreakable()
                    | sem.KeepWithNext()
                    | sem.Budgeted()
                    | sem.OptionalContent()
                    | sem.Truncated()
                    | sem.Spilled()
                    | sem.BestEffort()
                    | Budget()
                ),
                (
                    sem.Unbreakable()
                    | sem.KeepWithNext()
                    | sem.Budgeted()
                    | sem.OptionalContent()
                    | sem.Truncated()
                    | sem.Spilled()
                    | sem.BestEffort()
                    | Budget()
                ),
            ):
                return replace(left, node=sem.Group(())) == replace(right, node=sem.Group(())) and self.same_node(
                    left.node, right.node
                )
            case sem.ActionControl(), sem.ActionControl():
                return replace(left, on_trigger=right.on_trigger) == right if self.rebind_actions else left == right
            case sem.ActionControls(), sem.ActionControls():
                return replace(left, items=()) == replace(right, items=()) and self.same_children(
                    left.items, right.items
                )
            case sem.ControlGroup(), sem.ControlGroup():
                return replace(left, controls=()) == replace(right, controls=()) and self.same_children(
                    left.controls, right.controls
                )
            case ((sem.Choices() | sem.Entities()), (sem.Choices() | sem.Entities())):
                return isinstance(left.selection, Controlled | ValueBinding) and left == right
            case sem.Items(), sem.Items():
                return isinstance(left.opened, Controlled | ValueBinding) and left == right
            case sem.Details(), sem.Details():
                return (
                    isinstance(left.open, Controlled | ValueBinding)
                    and replace(left, children=()) == replace(right, children=())
                    and self.same_children(left.children, right.children)
                )
            case sem.Navigation(), sem.Navigation():
                return isinstance(left.current, Controlled | ValueBinding) and left == right
            case sem.Toggle(), sem.Toggle():
                # Session-owned values can change without changing the authored node.
                return isinstance(left.on, Controlled | ValueBinding) and left == right
            case Alternatives(), Alternatives():
                # Alternate realizations can remap source identities. Until rebinding
                # carries that correspondence, their handlers remain allocation inputs.
                comparison = _AllocationComparison(rebind_actions=False)
                return (
                    left.key == right.key
                    and left.priority == right.priority
                    and comparison.same_node(left.content, right.content)
                    and len(left.choices) == len(right.choices)
                    and all(
                        replace(a, node=sem.Group(())) == replace(b, node=sem.Group(()))
                        and comparison.same_node(a.node, b.node)
                        for a, b in zip(left.choices, right.choices, strict=True)
                    )
                )
            case sem.FallbackContent(), sem.FallbackContent():
                comparison = _AllocationComparison(rebind_actions=False)
                return comparison.same_node(left.primary, right.primary) and comparison.same_children(
                    left.alternates, right.alternates
                )
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
                | sem.Fields()
                | sem.Media()
                | sem.Figure()
                | sem.Paragraph()
                | sem.Table()
                | sem.Grid()
                | sem.Roster()
                | sem.Download()
                | sem.ActionControl()
                | sem.RoutedActionControl()
                | sem.Link()
                | sem.RoutedChoices()
                | sem.FormTrigger()
                | TextPresentation(),
                _,
            ):
                return left == right
            case NativeNode(), NativeNode():
                return left.semantics.same_allocation(left, right, self)
            case _:
                return False

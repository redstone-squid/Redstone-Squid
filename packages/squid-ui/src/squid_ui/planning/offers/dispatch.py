"""Dispatch semantic occurrences to target-independent realization handlers."""

from dataclasses import replace
from typing import assert_never

from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Alternatives,
    AtomicNode,
    Budget,
    TextPresentation,
)
from squid_ui.native import NativeValue
from squid_ui.planning.composition import (
    PlanningContext,
    SemanticOccurrence,
)
from squid_ui.planning.interaction_semantics import DisclosureToggle, ItemBack, ItemSummary, NavigationChoice
from squid_ui.planning.offers.content import (
    _alternatives_offers,
    _download_offers,
    _fallback_content_offers,
    _field_offers,
    _list_offers,
    _media_item_offers,
    _metric_offers,
    _optional_content_offers,
    _paragraph_offers,
    _progress_bar_offers,
    _roster_label_offers,
    _status_offers,
    _table_offers,
    _text_block_offers,
    _text_presentation_offers,
    _timestamp_offers,
    container_offers,
)
from squid_ui.planning.offers.interactions import (
    _action_control_offers,
    _choices_offers,
    _details_offers,
    _disclosure_toggle_offers,
    _entities_offers,
    _grid_offers,
    _item_back_offers,
    _item_branch_offers,
    _item_summary_offers,
    _items_offers,
    _navigation_choice_offers,
    _navigation_offers,
)
from squid_ui.planning.offers.policy import OfferPolicy, Offers, OfferScope
from squid_ui.planning.semantic_index import (
    FieldPart,
    ItemBranch,
    RosterLabel,
    RosterSection,
    StructuralGroup,
    TableCell,
)


def semantic_offers[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext, policy: OfferPolicy
) -> Offers[RenderTargetT, ContextT]:
    """Propose every realization `policy` allows for one occurrence.

    Ancestors decide how much text the occurrence may use and whether it may be shortened or spill.
    The planner then chooses among the offers. An empty tuple means the occurrence has no visible
    form on this target; an `OfferDiscovery` also explains why some forms are unsupported.
    """
    context.work.consume()
    value = occurrence.graph.units[occurrence.reference].value
    scope = OfferScope.for_occurrence(occurrence, context, policy)
    text_capacity = scope.text_capacity

    if isinstance(value, NativeValue):
        return value.semantics.offers(value, scope)
    if isinstance(value, sem.Themed):
        context = replace(context, palette=value.palette)
    scope = replace(scope, context=context, text_capacity=text_capacity)
    match value:
        case sem.Budgeted() | sem.Paged() if not policy.character_budgets:
            return ()
        case sem.FallbackContent():
            return _fallback_content_offers(scope, value)
        case sem.OptionalContent():
            return _optional_content_offers(scope, value)
        case Alternatives():
            return _alternatives_offers(scope, value)
        case RosterLabel():
            return _roster_label_offers(scope, value)
        case sem.List():
            return _list_offers(scope, value)
        case TextPresentation():
            return _text_presentation_offers(scope, value)
        case ItemSummary():
            return _item_summary_offers(scope, value)
        case ItemBack():
            return _item_back_offers(scope, value)
        case ItemBranch():
            return _item_branch_offers(scope, value)
        case sem.Items():
            return _items_offers(scope, value)
        case DisclosureToggle():
            return _disclosure_toggle_offers(scope, value)
        case sem.Details():
            return _details_offers(scope, value)
        case NavigationChoice():
            return _navigation_choice_offers(scope, value)
        case sem.Navigation():
            return _navigation_offers(scope, value)
        case sem.Grid():
            return _grid_offers(scope, value)
        case sem.Entities():
            return _entities_offers(scope, value)
        case sem.Choices() | sem.RoutedChoices():
            return _choices_offers(scope, value)
        case sem.ActionControl() | sem.RoutedActionControl() | sem.Link() | sem.FormTrigger() | sem.Toggle():
            return _action_control_offers(scope, value)
        case sem.Metric():
            return _metric_offers(scope, value)
        case sem.Timestamp() | sem.ZonedTimestamp():
            return _timestamp_offers(scope, value)
        case sem.Status():
            return _status_offers(scope, value)
        case sem.ProgressBar():
            return _progress_bar_offers(scope, value)
        case sem.Heading() | sem.Note() | sem.Code() | sem.Quote():
            return _text_block_offers(scope, value)
        case sem.Field():
            return _field_offers(scope, value)
        case sem.Paragraph():
            return _paragraph_offers(scope, value)
        case (
            sem.Group()
            | sem.Roster()
            | RosterSection()
            | sem.Stack()
            | sem.Section()
            | sem.Article()
            | sem.Aside()
            | sem.Fields()
            | sem.Media()
            | sem.Figure()
            | sem.Cluster()
            | sem.Themed()
            | sem.ActionControls()
            | sem.ControlGroup()
            | sem.Truncated()
            | sem.Spilled()
            | sem.BestEffort()
            | sem.Region()
            | sem.Paged()
            | sem.Budgeted()
            | StructuralGroup()
            | sem.Block()
            | Budget()
            | sem.Unbreakable()
            | sem.KeepWithNext()
        ):
            return container_offers(scope, value)
        case sem.Table():
            return _table_offers(scope, value)
        case sem.MediaItem():
            return _media_item_offers(scope, value)
        case sem.Download():
            return _download_offers(scope, value)
        # The enclosing table, list, gallery, or field draws these parts in its own realization.
        case sem.Column() | sem.TableRow() | sem.ListItem() | TableCell() | FieldPart():
            return ()
        # Atomic nodes are realized only by rules a target supplies for them.
        case AtomicNode():
            return ()
    assert_never(value)

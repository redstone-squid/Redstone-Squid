"""Dispatch semantic occurrences to target-independent realization handlers."""

from dataclasses import replace
from typing import assert_never, cast

from squid_ui import scene
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Alternatives,
    AtomicNode,
    Budget,
    TextPresentation,
)
from squid_ui.content.text import Text
from squid_ui.planning.composition import (
    DiscoveryCompletion,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    ResourceIdentity,
    SemanticOccurrence,
    SharedUse,
)
from squid_ui.planning.interaction_semantics import DisclosureToggle, ItemBack, ItemSummary, NavigationChoice
from squid_ui.planning.native_extensions import ComposableExtension
from squid_ui.planning.offers.content import (
    _alternatives_offers,
    _container_offers,
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
from squid_ui.planning.offers.policy import OfferPolicy, Offers, _OfferScope
from squid_ui.planning.offers.primitives import (
    _file_offers,
    _gallery_offers,
    _lines_offers,
    _primitive_container_offers,
    _primitive_control_offers,
    _primitive_text_offers,
    _separator_offers,
    _thumbnail_offers,
)
from squid_ui.planning.realizations import (
    FlowRealization,
    OpaqueRealization,
    Realization,
)
from squid_ui.planning.resources import EMPTY_COST, Axis
from squid_ui.planning.semantic_correspondence import interaction
from squid_ui.planning.semantic_index import (
    CardSlot,
    FieldPart,
    ItemBranch,
    RosterLabel,
    RosterSection,
    StructuralGroup,
    TableCell,
)
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import (
    Alt,
    Alts,
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
    scope = _OfferScope.for_occurrence(occurrence, context, policy)
    text_capacity = scope.text_capacity

    if isinstance(value, primitive.Extension):
        adapter = context.extensions.get(value.kind.name)
        if adapter is None or not policy.native_v2:
            ref = occurrence.graph.children[occurrence.reference][0]
            child = SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, "@fallback")),
                occurrence.graph,
                ref,
                occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            return (
                RealizationOffer[RenderTargetT, ContextT, Realization](
                    occurrence, "extension-fallback", FlowRealization(), (child,), flow=True
                ),
            )
        if not isinstance(adapter, ComposableExtension):
            return OfferDiscovery((), DiscoveryCompletion.UNSUPPORTED, "extension adapter needs compose()")
        if any(
            interaction(occurrence.graph.units[ref].value) is not None
            for ref in occurrence.graph.atoms(occurrence.reference)
        ):
            return OfferDiscovery(
                (), DiscoveryCompletion.UNSUPPORTED, "interactive extensions need typed control offers"
            )
        value = replace(adapter.compose(value.payload, context=context), kind=value.kind.name, version=value.version)
    if isinstance(value, primitive.RawItem):
        native = value.composition
        if not policy.native_v2 or native is None:
            return OfferDiscovery((), DiscoveryCompletion.UNSUPPORTED, "RawItem needs a native composition contract")
        if set(native.contract.cost.axes) - {Axis.COMPONENTS, Axis.DISPLAY_TEXT}:
            return OfferDiscovery((), DiscoveryCompletion.UNSUPPORTED, "native V2 additive resource axes")
        resource = SharedUse(
            ResourceIdentity("native", "native:" + "/".join(occurrence.identity.path)), value.factory(), EMPTY_COST
        )
        node = scene.Extension(
            value.kind,
            value.version,
            cast(dict[str, scene.JsonValue], {**value.payload, "resource": resource.identity.key}),
        )
        contract = replace(
            native.contract, shared_costs={**native.contract.shared_costs, resource.identity: EMPTY_COST}
        )
        return (
            RealizationOffer[RenderTargetT, ContextT, Realization](
                occurrence, "native-extension", OpaqueRealization(node, resource, native), atomic=contract
            ),
        )
    if isinstance(value, CardSlot):
        if policy.embed_limits is None:
            return ()
        text_capacity = policy.embed_limits[value.kind]
        value = value.text
    if any(isinstance(ancestor, primitive.Content) for ancestor in occurrence.ancestors()):
        text_capacity = context.capacities.get(Axis.CONTENT_TEXT, text_capacity)

    if isinstance(value, primitive.Lines) and isinstance(value.overflow, Alts):
        value = primitive.Text(
            Text.join(value.join, (line.primary if isinstance(line, Alt) else line for line in value.lines)),
            overflow=value.overflow,
            priority=value.priority,
        )
    if isinstance(value, sem.Themed):
        context = replace(context, palette=value.palette)
    scope = replace(scope, context=context, text_capacity=text_capacity)
    match value:
        case primitive.Lines():
            return _lines_offers(scope, value)
        case primitive.Text() | primitive.Heading() | primitive.Footer() | primitive.Code():
            return _primitive_text_offers(scope, value)
        case primitive.Gallery() | primitive.MediaCollection():
            return _gallery_offers(scope, value)
        case primitive.File():
            return _file_offers(scope, value)
        case primitive.Thumbnail():
            return _thumbnail_offers(scope, value)
        case primitive.Sep():
            return _separator_offers(scope, value)
        case (
            primitive.Row()
            | primitive.ControlGroup()
            | primitive.Section()
            | primitive.Panel()
            | primitive.Card()
            | primitive.Content()
        ):
            return _primitive_container_offers(scope, value)
        case (
            primitive.Button()
            | primitive.RoutedButton()
            | primitive.LinkButton()
            | primitive.PremiumButton()
            | primitive.SelectMenu()
            | primitive.RoutedSelect()
            | primitive.EntitySelect()
        ):
            return _primitive_control_offers(scope, value)
        case sem.Budgeted() | primitive.Budget() | sem.Paged() if not policy.character_budgets:
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
        case sem.Timestamp() | sem.ZonedTimestamp() | primitive.Time() | primitive.ZonedTime():
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
            | primitive.Budget()
            | primitive.Break()
            | StructuralGroup()
            | sem.Block()
            | Budget()
            | sem.Unbreakable()
            | sem.KeepWithNext()
        ):
            return _container_offers(scope, value)
        case sem.Table():
            return _table_offers(scope, value)
        case sem.MediaItem():
            return _media_item_offers(scope, value)
        case sem.Download():
            return _download_offers(scope, value)
        # The enclosing table, list, gallery, or field draws these parts in its own realization.
        case sem.Column() | sem.TableRow() | sem.ListItem() | primitive.GalleryItem() | TableCell() | FieldPart():
            return ()
        # Atomic nodes are realized only by rules a target supplies for them.
        case AtomicNode():
            return ()
        case _:
            assert_never(value)

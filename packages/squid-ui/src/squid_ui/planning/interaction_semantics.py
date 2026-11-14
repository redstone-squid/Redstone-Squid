"""Compare interaction behavior without inspecting dataclass fields or invoking handlers."""

from dataclasses import dataclass
from typing import Any, assert_never

from squid_ui import semantic as sem
from squid_ui.primitives import nodes as primitive


@dataclass(frozen=True, slots=True)
class NavigationChoice[ContextT]:
    """One destination retains the navigation owner when displayed independently."""

    navigation: sem.Navigation[ContextT]
    option: sem.NavOption


@dataclass(frozen=True, slots=True)
class DisclosureToggle[ContextT]:
    """The disclosure action is distinct from the content it reveals."""

    details: sem.Details[Any, ContextT]


@dataclass(frozen=True, slots=True)
class ItemSummary[ContextT]:
    items: sem.Items[Any, ContextT]
    item: sem.Item[Any, ContextT]


@dataclass(frozen=True, slots=True)
class ItemBack[ContextT]:
    items: sem.Items[Any, ContextT]


type Interaction[ContextT] = (
    sem.ActionControl[ContextT]
    | sem.RoutedActionControl
    | sem.Link
    | sem.Choices[ContextT]
    | sem.Entities[ContextT]
    | sem.RoutedChoices
    | sem.FormTrigger[ContextT]
    | sem.Toggle[ContextT]
    | NavigationChoice[ContextT]
    | DisclosureToggle[ContextT]
    | ItemSummary[ContextT]
    | ItemBack[ContextT]
    | primitive.Button[ContextT]
    | primitive.RoutedButton
    | primitive.LinkButton
    | primitive.PremiumButton
    | primitive.SelectMenu[ContextT]
    | primitive.RoutedSelect
    | primitive.EntitySelect[ContextT]
)


def interaction_changes[ContextT](
    source: Interaction[ContextT] | None, target: Interaction[ContextT] | None
) -> frozenset[str]:
    """Return changed behavior attributes; labels and visual styling are presentation.

    Comparing against None describes all the behavior a control introduces or removes.
    Ownership values include their application callbacks, but comparing them never
    invokes those callbacks. New interaction types must explicitly join this contract.
    """
    if source is None:
        return frozenset() if target is None else interaction_changes(target, None)
    match source:
        case primitive.Button():
            other = target if isinstance(target, primitive.Button) else None
            changes = (
                ("on_click", other is None or source.on_click != other.on_click),
                ("disabled", other is None or source.disabled != other.disabled),
                ("mode", other is None or source.mode != other.mode),
                ("guard", other is None or source.guard != other.guard),
                ("busy", other is None or source.busy != other.busy),
                ("record", other is None or source.record != other.record),
                (
                    "form",
                    (source.form if isinstance(source, primitive.FormButton) else None)
                    != (other.form if isinstance(other, primitive.FormButton) else None),
                ),
            )
        case primitive.RoutedButton():
            other = target if isinstance(target, primitive.RoutedButton) else None
            changes = (
                ("route_id", other is None or source.route_id != other.route_id),
                ("disabled", other is None or source.disabled != other.disabled),
            )
        case primitive.PremiumButton():
            other = target if isinstance(target, primitive.PremiumButton) else None
            changes = (("sku_id", other is None or source.sku_id != other.sku_id),)
        case primitive.LinkButton():
            other = target if isinstance(target, primitive.LinkButton) else None
            changes = (
                ("url", other is None or source.url != other.url),
                ("disabled", other is None or source.disabled != other.disabled),
            )
        case primitive.SelectMenu() | primitive.RoutedSelect():
            other = target if isinstance(target, type(source)) else None
            changes = (
                ("options", other is None or source.options != other.options),
                ("minimum", other is None or source.min_values != other.min_values),
                ("maximum", other is None or source.max_values != other.max_values),
                ("disabled", other is None or source.disabled != other.disabled),
            )
            if isinstance(source, primitive.SelectMenu):
                selected = target if isinstance(target, primitive.SelectMenu) else None
                changes += (
                    ("on_select", selected is None or source.on_select != selected.on_select),
                    ("mode", selected is None or source.mode != selected.mode),
                    ("routes", selected is None or source.routes != selected.routes),
                )
            else:
                routed = target if isinstance(target, primitive.RoutedSelect) else None
                changes += (("route_id", routed is None or source.route_id != routed.route_id),)
        case primitive.EntitySelect():
            other = target if isinstance(target, primitive.EntitySelect) else None
            changes = (
                ("entity_type", other is None or source.entity_type != other.entity_type),
                ("on_select", other is None or source.on_select != other.on_select),
                ("default_values", other is None or source.default_values != other.default_values),
                ("conversation_types", other is None or source.conversation_types != other.conversation_types),
                ("minimum", other is None or source.min_values != other.min_values),
                ("maximum", other is None or source.max_values != other.max_values),
                ("disabled", other is None or source.disabled != other.disabled),
                ("mode", other is None or source.mode != other.mode),
            )
        case ItemSummary():
            other = target if isinstance(target, ItemSummary) else None
            changes = (
                ("opened", other is None or source.items.opened != other.items.opened),
                ("item", other is None or source.item.key != other.item.key),
            )
        case ItemBack():
            other = target if isinstance(target, ItemBack) else None
            changes = (("opened", other is None or source.items.opened != other.items.opened),)
        case DisclosureToggle():
            other = target if isinstance(target, DisclosureToggle) else None
            changes = (("open", other is None or source.details.open != other.details.open),)
        case NavigationChoice():
            other = target if isinstance(target, NavigationChoice) else None
            changes = (
                ("current", other is None or source.navigation.current != other.navigation.current),
                ("destination", other is None or source.option.key != other.option.key),
                ("available", other is None or source.option.available != other.option.available),
            )
        case sem.ActionControl():
            other = target if isinstance(target, sem.ActionControl) else None
            changes = (
                ("on_trigger", other is None or source.on_trigger != other.on_trigger),
                ("available", other is None or source.available != other.available),
                ("mode", other is None or source.mode != other.mode),
                ("guard", other is None or source.guard != other.guard),
                ("busy", other is None or source.busy != other.busy),
                ("record", other is None or source.record != other.record),
            )
        case sem.RoutedActionControl():
            other = target if isinstance(target, sem.RoutedActionControl) else None
            changes = (
                ("route_id", other is None or source.route_id != other.route_id),
                ("available", other is None or source.available != other.available),
            )
        case sem.Link():
            other = target if isinstance(target, sem.Link) else None
            changes = (("url", other is None or source.url != other.url),)
        case sem.Choices():
            other = target if isinstance(target, sem.Choices) else None
            changes = (
                ("choices", other is None or source.choices != other.choices),
                ("selection", other is None or source.selection != other.selection),
                ("minimum", other is None or source.minimum != other.minimum),
                ("maximum", other is None or source.maximum != other.maximum),
            )
        case sem.Entities():
            other = target if isinstance(target, sem.Entities) else None
            changes = (
                ("entity_type", other is None or source.entity_type != other.entity_type),
                ("choices", other is None or source.choices != other.choices),
                ("selection", other is None or source.selection != other.selection),
                ("minimum", other is None or source.minimum != other.minimum),
                ("maximum", other is None or source.maximum != other.maximum),
                ("conversation_types", other is None or source.conversation_types != other.conversation_types),
            )
        case sem.RoutedChoices():
            other = target if isinstance(target, sem.RoutedChoices) else None
            changes = (
                ("choices", other is None or source.choices != other.choices),
                ("route_id", other is None or source.route_id != other.route_id),
                ("minimum", other is None or source.minimum != other.minimum),
                ("maximum", other is None or source.maximum != other.maximum),
                ("available", other is None or source.available != other.available),
                ("selected", other is None or source.selected != other.selected),
            )
        case sem.FormTrigger():
            other = target if isinstance(target, sem.FormTrigger) else None
            changes = (
                ("spec", other is None or source.spec != other.spec),
                ("on_submit", other is None or source.on_submit != other.on_submit),
                ("mode", other is None or source.mode != other.mode),
                ("guard", other is None or source.guard != other.guard),
                ("record", other is None or source.record != other.record),
            )
        case sem.Toggle():
            other = target if isinstance(target, sem.Toggle) else None
            changes = (
                ("on", other is None or source.on != other.on),
                ("available", other is None or source.available != other.available),
            )
        case _:
            assert_never(source)
    changed = frozenset(name for name, differs in changes if differs)
    return changed | {"kind"} if type(source) is not type(target) else changed

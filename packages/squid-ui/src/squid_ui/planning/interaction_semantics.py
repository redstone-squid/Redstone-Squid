"""Compare interaction behavior without inspecting dataclass fields or invoking handlers."""

from typing import assert_never

from squid_ui import semantic as sem

type Interaction[ContextT] = (
    sem.ActionControl[ContextT]
    | sem.RoutedActionControl
    | sem.Link
    | sem.Choices[ContextT]
    | sem.Entities[ContextT]
    | sem.RoutedChoices
    | sem.FormTrigger[ContextT]
    | sem.Toggle[ContextT]
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

"""Compatibility imports for semantic indexing and correspondence."""

from squid_ui.planning.semantic_correspondence import (
    CheckedAlternative,
    SourceScope,
    budget_floor,
    check_alternative,
    interaction,
    source_text,
)
from squid_ui.planning.semantic_index import (
    CardSlot,
    FieldPart,
    ItemBranch,
    RosterLabel,
    RosterMembers,
    RosterSection,
    SemanticGraph,
    SemanticUnit,
    SemanticValue,
    StructuralGroup,
    TableCell,
    UnsupportedSemanticError,
    semantic_graph,
    variant_alternatives,
)

__all__ = [
    "CardSlot",
    "CheckedAlternative",
    "FieldPart",
    "ItemBranch",
    "RosterLabel",
    "RosterMembers",
    "RosterSection",
    "SemanticGraph",
    "SemanticUnit",
    "SemanticValue",
    "SourceScope",
    "StructuralGroup",
    "TableCell",
    "UnsupportedSemanticError",
    "budget_floor",
    "check_alternative",
    "interaction",
    "semantic_graph",
    "source_text",
    "variant_alternatives",
]

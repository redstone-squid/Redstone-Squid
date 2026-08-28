"""Stable diagnostics emitted while measuring a concrete layout."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum

from squid_ui.errors import LayoutError


class SolveNoteCode(StrEnum):
    """Stable identities for diagnostics emitted by the measured solver."""

    CLAMP_BUTTON_LABEL = "clamp.button_label"
    CLAMP_SELECT_OPTIONS = "clamp.select_options"
    CLAMP_SELECT_OPTION_TEXT = "clamp.select_option_text"
    CLAMP_SELECT_PLACEHOLDER = "clamp.select_placeholder"
    CLAMP_SECTION_TEXTS = "clamp.section_texts"
    CLAMP_GALLERY_ITEMS = "clamp.gallery_items"
    CLAMP_EMBED_TEXT = "clamp.embed_text"
    CLAMP_EMBED_FIELDS = "clamp.embed_fields"
    NODE_DROPPED = "degradation.node_dropped"
    ALTERNATE = "degradation.alternate"
    ALTERNATE_EXHAUSTED = "degradation.alternate_exhausted"
    TRUNCATED = "degradation.truncated"
    NEVER_CLAMPED = "degradation.never_clamped"
    CHROME_DROP = "degradation.chrome_drop"
    CONDENSED = "degradation.condensed"
    CONDENSE_TRUNCATED = "degradation.condense_truncated"
    SPILL_ALTERNATES = "degradation.spill_alternates"
    SPILLED = "degradation.spilled"
    SPILL_DROPPED = "degradation.spill_dropped"
    NEVER_BUDGET = "failure.never_budget"
    BUDGET_FLOOR = "failure.budget_floor"
    BEST_EFFORT_FLOOR = "degradation.best_effort_floor"
    VARIANT_STEP = "adaptation.variant_step"
    SEMANTIC_FALLBACK = "degradation.semantic_fallback"
    OPTIONAL_DROPPED = "degradation.optional_dropped"
    VARIANT_REFORMATTED = "degradation.variant_reformatted"
    VARIANT_LOSSY = "degradation.variant_lossy"
    PAGINATE_PER_FALLBACK = "degradation.paginate_per_fallback"
    COMPONENT_BUDGET = "degradation.component_budget"
    TEXT_BUDGET = "degradation.text_budget"


class SolveNoteSeverity(Enum):
    """How a solver note affects feasibility and reporting."""

    ADAPTATION = "adaptation"
    """The layout took another shape and lost nothing; `strict=True` accepts this.

    Stepping a ladder to an exact rung is the case that matters: paginating a long region
    or splitting it across cards shows every word the author wrote, so a caller who asked
    for no degradation has not been given any.
    """

    CLAMP = "clamp"
    DEGRADATION = "degradation"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class SolveNote:
    """A stable solver diagnostic whose meaning does not depend on message wording."""

    code: SolveNoteCode
    message: str
    severity: SolveNoteSeverity = SolveNoteSeverity.DEGRADATION

    def __str__(self) -> str:
        return self.message


def lossy_notes(notes: Sequence[SolveNote]) -> list[SolveNote]:
    """The notes `strict=True` refuses: everything that is not lossless adaptation."""
    return [note for note in notes if note.severity is not SolveNoteSeverity.ADAPTATION]


def note(
    code: SolveNoteCode,
    message: str,
    severity: SolveNoteSeverity = SolveNoteSeverity.DEGRADATION,
) -> SolveNote:
    """Build one solver diagnostic."""
    return SolveNote(code, message, severity)


class LayoutOverflowError(LayoutError):
    """The document cannot fit its hard constraints into Discord's budgets."""

    def __init__(self, notes: list[SolveNote]) -> None:
        super().__init__("; ".join(note.message for note in notes))
        self.notes = notes

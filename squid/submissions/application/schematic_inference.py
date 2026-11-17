"""Turn schematic evidence into suggestions for an explicitly reviewed draft."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from squid.builds.domain.credits import name_credits
from squid.core.errors import JSONValue
from squid.schematics.domain.models import SchematicAnalysis, SchematicMetrics
from squid.submissions.application.drafts import StoredDraft
from squid.submissions.application.forms import SubmissionFormService
from squid.submissions.domain import match_choice
from squid.submissions.domain.schematics import DraftSchematicState
from squid.versions.application.services import VersionService


@dataclass(frozen=True, slots=True)
class SchematicFieldSuggestion:
    field_id: str
    value: JSONValue
    evidence: str


@dataclass(frozen=True, slots=True)
class SchematicDetection:
    """A semantic detector result; no detector currently recognizes these fields."""

    field_id: str
    status: Literal["not_detected"] = "not_detected"
    value: JSONValue = None
    reason: str = "Detection is not implemented."


@dataclass(frozen=True, slots=True)
class SchematicSubmissionSuggestions:
    upload_id: UUID
    state: DraftSchematicState
    draft_revision: int
    suggestions: tuple[SchematicFieldSuggestion, ...] = ()
    detections: tuple[SchematicDetection, ...] = ()
    measurements: SchematicMetrics | None = None


class SchematicSubmissionInference:
    """Suggest form-valid facts without changing answers or asserting verified credits."""

    def __init__(self, forms: SubmissionFormService, versions: VersionService) -> None:
        self._forms = forms
        self._versions = versions

    def detect(self, analysis: SchematicAnalysis) -> tuple[SchematicDetection, ...]:
        """Reserved API for recognition of mechanism geometry and timing."""
        return tuple(
            SchematicDetection(field_id)
            for field_id in (
                "category",
                "opening_width",
                "opening_height",
                "opening_depth",
                "door_orientation",
                "opening_time",
                "closing_time",
                "visible_opening_time",
                "visible_closing_time",
            )
        )

    async def suggest(
        self, draft: StoredDraft, upload_id: UUID, state: DraftSchematicState, analysis: SchematicAnalysis | None
    ) -> SchematicSubmissionSuggestions:
        if analysis is None or state is not DraftSchematicState.READY:
            return SchematicSubmissionSuggestions(upload_id, state, draft.snapshot.revision)
        manifest = await self._forms.manifest_revision(
            draft.snapshot.schema_id, draft.snapshot.schema_revision, locale=None
        )
        metrics = analysis.metrics
        candidates: list[SchematicFieldSuggestion] = []
        bounds = metrics.dimensions
        for field_id, value in (
            ("capture_width", bounds.width),
            ("capture_height", bounds.height),
            ("capture_depth", bounds.length),
        ):
            candidates.append(
                SchematicFieldSuggestion(
                    field_id,
                    value,
                    "Occupied schematic bounds (X/Y/Z). Confirm the selection and width/depth orientation; "
                    "these are not the door opening or rules-adjusted volume.",
                )
            )
        if metrics.declared_name:
            candidates.append(
                SchematicFieldSuggestion("display_name", metrics.declared_name, "Embedded schematic name.")
            )
        if metrics.declared_author and 0 < len(metrics.declared_author.strip()) <= 255:
            credits: JSONValue = [credit.to_json() for credit in name_credits([metrics.declared_author])]
            candidates.append(
                SchematicFieldSuggestion("credits", credits, "Embedded author text; confirm attribution and role.")
            )
        if metrics.source_data_version is not None:
            label = await self._versions.label_for_data_version(metrics.source_data_version)
            if label:
                options = await self._forms.options("approved_source_versions", draft.snapshot.category, locale=None)
                key = match_choice(label, options.options)
                if key is not None:
                    candidates.append(
                        SchematicFieldSuggestion(
                            "source_version",
                            key,
                            "Declared schematic data version; this does not establish version compatibility.",
                        )
                    )
        if metrics.block_counts is not None and metrics.block_count > 0:
            names = {state.partition("[")[0] for state, count in metrics.block_counts.items() if count > 0}
            absent = [
                label
                for block, label in (
                    ("minecraft:slime_block", "Slimeless"),
                    ("minecraft:honey_block", "Honeyless"),
                    ("minecraft:observer", "Observerless"),
                )
                if block not in names
            ]
            options = await self._forms.options("approved_restrictions", draft.snapshot.category, locale=None)
            keys: list[JSONValue] = [
                key for label in absent if (key := match_choice(label, options.options)) is not None
            ]
            if keys:
                candidates.append(
                    SchematicFieldSuggestion(
                        "restrictions", keys, "Components absent from the uploaded selection's block inventory."
                    )
                )
        suggestions: list[SchematicFieldSuggestion] = []
        if manifest is not None:
            for candidate in candidates:
                answers = {**draft.snapshot.answers, candidate.field_id: candidate.value}
                errors = manifest.validate_answers(
                    draft.snapshot.category, answers, origin=draft.origin, require_complete=False
                )
                visible = manifest.visible_answers(draft.snapshot.category, answers, origin=draft.origin)
                if candidate.field_id not in errors and candidate.field_id in visible:
                    suggestions.append(candidate)
        return SchematicSubmissionSuggestions(
            upload_id, state, draft.snapshot.revision, tuple(suggestions), self.detect(analysis), metrics
        )

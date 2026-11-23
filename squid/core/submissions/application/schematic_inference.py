"""Turn schematic evidence into suggestions for an explicitly reviewed draft."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from squid.core.builds.domain.credits import name_credits
from squid.core.errors import JSONValue
from squid.core.schematics.domain.doors.detection import DoorDetection
from squid.core.schematics.domain.models import SchematicAnalysis, SchematicMetrics
from squid.core.submissions.application.drafts import StoredDraft
from squid.core.submissions.application.forms import SubmissionFormService
from squid.core.submissions.domain import match_choice
from squid.core.submissions.domain.schematics import DraftSchematicState
from squid.core.versions.application.services import VersionService


@dataclass(frozen=True, slots=True)
class SchematicFieldSuggestion:
    field_id: str
    value: JSONValue
    evidence: str


@dataclass(frozen=True, slots=True)
class SchematicDetection:
    """A field-level conclusion drawn from schematic evidence."""

    field_id: str
    status: Literal["detected", "ambiguous", "not_detected"] = "not_detected"
    value: JSONValue = None
    reason: str = "No measurement is available for this field."


@dataclass(frozen=True, slots=True)
class SchematicSubmissionSuggestions:
    upload_id: UUID
    state: DraftSchematicState
    draft_revision: int
    suggestions: tuple[SchematicFieldSuggestion, ...] = ()
    detections: tuple[SchematicDetection, ...] = ()
    measurements: SchematicMetrics | None = None
    door: DoorDetection | None = None


class SchematicSubmissionInference:
    """Suggest form-valid facts without changing answers or asserting verified credits."""

    def __init__(self, forms: SubmissionFormService, versions: VersionService) -> None:
        self._forms = forms
        self._versions = versions

    def detect(self, analysis: SchematicAnalysis) -> tuple[SchematicDetection, ...]:
        """Suggest a measurement only when every hallway interpretation agrees.

        Measure the opening across the passage. Depth is the span of a matched
        closed arrangement, which may be much shorter than the captured tunnel.
        Missing patterns leave depth unknown. A vertical axis alone cannot
        distinguish a skydoor from a trapdoor.
        """
        fields = (
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
        detections = {field: SchematicDetection(field) for field in fields}
        if analysis.door is None or not analysis.door.candidates:
            return tuple(detections.values())
        values: dict[str, set[int | str | None]] = {
            field: set() for field in ("opening_width", "opening_height", "opening_depth", "door_orientation")
        }
        for hall in analysis.door.candidates:
            for width, height in hall.opening_sizes:
                values["opening_width"].add(width)
                values["opening_height"].add(height)
            values["door_orientation"].add("door" if hall.axis != 1 else None)
            if not hall.patterns:
                values["opening_depth"].add(None)
            for match in hall.patterns:
                distances = [pos[hall.axis] for pos in match.blocks]
                values["opening_depth"].add(max(distances) - min(distances) + 1)
        for field, options in values.items():
            if len(options) == 1 and None not in options:
                detections[field] = SchematicDetection(
                    field, "detected", next(iter(options)), "All candidate hallway interpretations agree."
                )
            elif len(options) > 1:
                detections[field] = SchematicDetection(field, "ambiguous", reason="Hallway interpretations differ.")
        return tuple(detections.values())

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
        detections = self.detect(analysis)
        if draft.snapshot.category == "door":
            candidates.extend(
                SchematicFieldSuggestion(item.field_id, item.value, item.reason)
                for item in detections
                if item.status == "detected"
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
            upload_id, state, draft.snapshot.revision, tuple(suggestions), detections, metrics, analysis.door
        )

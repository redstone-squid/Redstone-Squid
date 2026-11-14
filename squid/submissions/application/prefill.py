"""Convert collected build details into answers for a submission form.

Inference produces a BuildDraft with build field names and human-readable tag
names. Prefill maps these to the form's field IDs and stable option keys before
a retained submission draft is created. Unrecognized or invalid values are
reported for review, while the original BuildDraft remains available to callers.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from squid.builds.domain import BuildDraft, UnresolvedTaxonomy
from squid.core.errors import JSONValue
from squid.submissions.application.forms import SubmissionFormService
from squid.submissions.domain import ChoiceOption, FormManifest, SubmissionOrigin, match_choice


@dataclass(frozen=True, slots=True)
class SubmissionPrefill:
    """Form answers plus facts requiring explicit correction instead of silent substitution."""

    category: str | None
    answers: dict[str, JSONValue]
    unresolved: tuple[str, ...]


async def prefill_submission(
    source: BuildDraft,
    forms: SubmissionFormService,
    *,
    origin: SubmissionOrigin,
) -> SubmissionPrefill:
    """Resolve display labels to stable option keys without guessing missing facts."""
    category = source.category.value.lower() if source.category is not None else None
    if category is None:
        return SubmissionPrefill(None, {}, ("category",))
    manifest = await forms.manifest(locale=None)
    options: dict[str, tuple[ChoiceOption, ...]] = {}
    for field in manifest.fields_for(category):
        if field.option_source is not None:
            options[field.option_source] = (await forms.options(field.option_source, category, locale=None)).options
    return project_submission(source, manifest, options, origin=origin)


def project_submission(
    source: BuildDraft,
    manifest: FormManifest,
    options: Mapping[str, Sequence[ChoiceOption]],
    *,
    origin: SubmissionOrigin,
) -> SubmissionPrefill:
    """Project facts and retain unresolved field names for correction."""
    category = source.category.value.lower() if source.category is not None else None
    if category is None:
        return SubmissionPrefill(None, {}, ("category",))
    answers: dict[str, JSONValue] = {
        "schematic_visibility": "reviewer_only",
        "ai_generated": bool(source.ai_generated),
    }
    values: dict[str, JSONValue] = {
        "display_name": source.display_name,
        "description": source.description,
        **(
            {"credits": [credit.to_json() for credit in source.credits]}
            if manifest.revision >= 5
            else {"creators": list(source.creators_ign)}
        ),
        "capture_width": source.width,
        "capture_height": source.height,
        "capture_depth": source.depth,
        "version_compatibility": source.version_spec,
        "completion": source.completion_time,
    }
    if category == "door":
        values.update(
            {
                "opening_width": source.door_width,
                "opening_height": source.door_height,
                "opening_depth": source.door_depth,
                "door_orientation": source.door_orientation.lower() if source.door_orientation else None,
                "opening_time": source.normal_opening_time,
                "closing_time": source.normal_closing_time,
                "visible_opening_time": source.visible_opening_time,
                "visible_closing_time": source.visible_closing_time,
            }
        )
    elif category == "extender":
        orientation = {"Horizontal": "horizontal", "Upward": "vertical_up", "Downward": "vertical_down"}.get(
            source.extender_orientation or ""
        )
        values.update({"movement_orientation": orientation, "extension_length": source.extension_length})
    answers.update({key: value for key, value in values.items() if value is not None and value != []})
    versions = options.get("approved_source_versions", ())
    exact_version = match_choice(
        source.version_spec or (source.versions[0] if len(source.versions) == 1 else ""), versions
    )
    if exact_version is not None:
        answers["source_version"] = exact_version
    # Names the taxonomy could not resolve still go through the catalog match below, so they become
    # proposals and reach the finalized build as unresolved names instead of disappearing.
    unresolved_taxonomy = UnresolvedTaxonomy.read(source.extra_info)
    restrictions = [
        *source.wiring_placement_restrictions,
        *source.animated_restrictions,
        *source.component_restrictions,
        *source.miscellaneous_restrictions,
        *unresolved_taxonomy.restriction_names(),
    ]
    patterns = [*source.patterns, *unresolved_taxonomy.patterns]
    unresolved: list[str] = []
    for field_id, proposed_id, names, catalog in (
        ("restrictions", "restriction_proposals", restrictions, "approved_restrictions"),
        ("patterns", "pattern_proposals", patterns, "approved_patterns"),
    ):
        if not names:
            continue
        known: list[JSONValue] = []
        proposed: list[JSONValue] = []
        for name in dict.fromkeys(names):
            key = match_choice(name, options.get(catalog, ()))
            if key is None:
                proposed.append(name)
            else:
                known.append(key)
        if known:
            answers[field_id] = known
        if proposed:
            answers[proposed_id] = proposed
    # Keep only values the pinned manifest can accept. The candidate snapshot retains
    # the original facts, so rejected values remain reviewable instead of being invented.
    for issue in manifest.validate_answers(category, answers, origin=origin, require_complete=False):
        answers.pop(issue, None)
        unresolved.append(issue)
    # Drop facts for fields this origin is not asked about, such as the exported version on Discord.
    visible = manifest.visible_answers(category, answers, origin=origin)
    answers = {field_id: value for field_id, value in answers.items() if field_id in visible}
    return SubmissionPrefill(category, answers, tuple(dict.fromkeys(unresolved)))

"""Translate transport or inference facts into the shared form vocabulary."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from squid.builds.domain import BuildDraft
from squid.core.errors import JSONValue
from squid.submissions.application.forms import SubmissionFormService
from squid.submissions.domain import ChoiceOption, FormManifest, SubmissionOrigin


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
        "include_inventories": False,
        "include_free_text": False,
        "ai_generated": bool(source.ai_generated),
    }
    values: dict[str, JSONValue] = {
        "display_name": source.display_name,
        "description": source.description or source.extra_info.get("user"),
        "creators": list(source.creators_ign),
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
        orientation = {"Horizontal": "horizontal", "Up": "vertical_up", "Down": "vertical_down"}.get(
            source.extender_orientation or ""
        )
        values.update({"movement_orientation": orientation, "extension_length": source.extension_length})
    answers.update({key: value for key, value in values.items() if value is not None and value != []})
    versions = options.get("approved_source_versions", ())
    exact_version = _known(source.version_spec or (source.versions[0] if len(source.versions) == 1 else ""), versions)
    if exact_version is not None:
        answers["source_version"] = exact_version
    restrictions = [
        *source.wiring_placement_restrictions,
        *source.animated_restrictions,
        *source.component_restrictions,
        *source.miscellaneous_restrictions,
    ]
    unresolved: list[str] = []
    for field_id, proposed_id, names, catalog in (
        ("restrictions", "restriction_proposals", restrictions, "approved_restrictions"),
        ("patterns", "pattern_proposals", source.patterns, "approved_patterns"),
    ):
        if not names:
            continue
        known: list[JSONValue] = []
        proposed: list[JSONValue] = []
        for name in dict.fromkeys(names):
            key = _known(name, options.get(catalog, ()))
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
    return SubmissionPrefill(category, answers, tuple(dict.fromkeys(unresolved)))


def _known(value: str, choices: Sequence[ChoiceOption]) -> str | None:
    matches = {
        choice.value for choice in choices if value.casefold() in {choice.value.casefold(), choice.label.casefold()}
    }
    return next(iter(matches)) if len(matches) == 1 else None

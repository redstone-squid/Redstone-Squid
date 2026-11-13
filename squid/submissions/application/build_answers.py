"""Convert a saved build to edit-form answers, and changed answers back to a build patch.

The Discord editor edits saved builds with the same sections and pickers as submission drafts
(see `squid.submissions.application.edit_form`). This module is the bridge in both directions:

1. `answers_from_build` turns a loaded build into answers keyed by edit-form field IDs. Catalogue
   fields (restrictions, patterns, showcase tags, versions) hold catalogue keys, as a draft's do.
2. The editor changes some answers.
3. `patch_from_answers` compares the answers before and after and returns a `BuildEditPatch`
   carrying only what changed, in the patch's own vocabulary.

Restrictions and patterns are stored on a build as display names, and some match no approved
tag. Those names are not in the picker, so they travel beside the answers as `unresolved` names:
the editor lists them, the user may remove them, and whatever remains is written back so a save
never drops them silently. The build edit command turns the names back into tags, so it does not
matter which restriction list a resolved name is sent in.

An answer that did not change never reaches the patch, so opening and saving a build without
touching a field cannot rewrite how that field is stored.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import UnresolvedTaxonomy
from squid.builds.domain.taxonomy import RESTRICTION_FIELDS, RestrictionField
from squid.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.errors import JSONValue
from squid.submissions.domain import ChoiceOption, match_choice
from squid.tags.domain import TagSemanticKind

type CatalogueOptions = Mapping[str, Sequence[ChoiceOption]]
"""Choices by option source, such as `approved_restrictions`, as the form option service returns them."""

_EXTENDER_DIRECTIONS = {"Horizontal": "horizontal", "Upward": "vertical_up", "Downward": "vertical_down"}
_PATCH_EXTENDER_DIRECTIONS = {answer: stored for stored, answer in _EXTENDER_DIRECTIONS.items()}

_SERVER_KEYS = {"server_ip": "server_ip", "coordinates": "coordinates", "command_to_get_to_build": "command_to_build"}


@dataclass(frozen=True, slots=True)
class BuildAnswers:
    """A saved build as edit-form answers, plus the names no catalogue entry matches."""

    answers: dict[str, JSONValue]
    unresolved: UnresolvedTaxonomy


def answers_from_build(build: Build, options: CatalogueOptions) -> BuildAnswers:
    """Return `build` as edit-form answers.

    `options` must hold the `approved_restrictions` and `approved_patterns` choices; names that
    match none of them become `unresolved` names, after the build's existing unresolved names.
    """
    server = build.extra_info.get("server_info", {})
    answers: dict[str, JSONValue] = {
        "display_name": build.display_name,
        "description": build.description,
        "creators": list(build.creators_ign),
        "capture_width": build.width,
        "capture_height": build.height,
        "capture_depth": build.depth,
        "versions": list(build.versions),
        "version_compatibility": build.version_spec,
        "completion": build.completion_time,
        "showcase_tags": [
            assignment.definition.stable_key
            for assignment in build.tags
            if assignment.definition.semantic_kind is TagSemanticKind.SHOWCASE
        ],
        "image_urls": list(build.image_urls),
        "video_urls": list(build.video_urls),
        "world_download_urls": list(build.world_download_urls),
        **{field_id: cast(str | None, server.get(key)) for field_id, key in _SERVER_KEYS.items()},
    }
    unresolved = UnresolvedTaxonomy.read(build.extra_info)
    restriction_keys: list[JSONValue] = []
    for field_name in RESTRICTION_FIELDS:
        for name in getattr(build, field_name):
            key = match_choice(name, options.get("approved_restrictions", ()))
            if key is None:
                unresolved = unresolved.add(restrictions=((field_name, name),))
            else:
                restriction_keys.append(key)
    answers["restrictions"] = restriction_keys
    if isinstance(build, DoorBuild | ExtenderBuild):
        pattern_keys: list[JSONValue] = []
        for name in build.patterns:
            key = match_choice(name, options.get("approved_patterns", ()))
            if key is None:
                unresolved = unresolved.add(patterns=(name,))
            else:
                pattern_keys.append(key)
        answers["patterns"] = pattern_keys
    if isinstance(build, DoorBuild):
        answers |= {
            "opening_width": build.door_width,
            "opening_height": build.door_height,
            "opening_depth": build.door_depth,
            "door_orientation": build.orientation.lower() if build.orientation is not None else None,
            "opening_time": build.normal_opening_time,
            "visible_opening_time": build.visible_opening_time,
            "closing_time": build.normal_closing_time,
            "visible_closing_time": build.visible_closing_time,
        }
    if isinstance(build, ExtenderBuild):
        answers |= {
            "movement_orientation": _EXTENDER_DIRECTIONS.get(build.orientation or ""),
            "extension_length": build.extension_length,
            "extension_time": build.extension_time,
            "retraction_time": build.retraction_time,
        }
    return BuildAnswers(answers, unresolved)


def patch_from_answers(
    before: BuildAnswers, after: Mapping[str, JSONValue], unresolved: UnresolvedTaxonomy, options: CatalogueOptions
) -> BuildEditPatch:
    """Return a patch that changes only what differs between `before` and `after`.

    `unresolved` is the set of unresolved names the user kept; removing one counts as a change.
    `options` must hold the catalogue choices used by `answers_from_build`, to turn keys back
    into names.
    """

    def changed(*field_ids: str) -> bool:
        return any(before.answers.get(field_id) != after.get(field_id) for field_id in field_ids)

    changes: dict[str, object] = {}
    simple = {
        "display_name": "display_name",
        "description": "extra_user_info",
        "version_compatibility": "version_spec",
        "completion": "completion_time",
        "extension_length": "extension_length",
        "extension_time": "extension_time",
        "retraction_time": "retraction_time",
        "opening_time": "normal_opening_time",
        "visible_opening_time": "visible_opening_time",
        "closing_time": "normal_closing_time",
        "visible_closing_time": "visible_closing_time",
        **{field_id: field_id for field_id in _SERVER_KEYS},
    }
    for field_id, attribute in simple.items():
        if changed(field_id):
            changes[attribute] = _blank_as_none(after.get(field_id))
    lists = {
        "creators": "creators_ign",
        "versions": "versions",
        "showcase_tags": "showcase_tags",
        "image_urls": "image_urls",
        "video_urls": "video_urls",
        "world_download_urls": "world_download_urls",
    }
    for field_id, attribute in lists.items():
        if changed(field_id):
            changes[attribute] = list(_strings(after.get(field_id)))
    if changed("capture_width", "capture_height", "capture_depth"):
        changes["dimensions"] = _dimensions(after, "capture_width", "capture_height", "capture_depth")
    if changed("opening_width", "opening_height", "opening_depth"):
        changes["door_dimensions"] = _dimensions(after, "opening_width", "opening_height", "opening_depth")
    if changed("door_orientation"):
        orientation = after.get("door_orientation")
        changes["door_orientation_type"] = orientation.title() if isinstance(orientation, str) else None
    if changed("movement_orientation"):
        changes["extender_orientation"] = _PATCH_EXTENDER_DIRECTIONS.get(str(after.get("movement_orientation")))

    if changed("restrictions") or unresolved.restrictions != before.unresolved.restrictions:
        by_field: dict[RestrictionField, list[str]] = {field_name: [] for field_name in RESTRICTION_FIELDS}
        # The edit command files resolved names under their tag's restriction type itself.
        by_field["miscellaneous_restrictions"] = _names(after.get("restrictions"), options, "approved_restrictions")
        for field_name, name in unresolved.restrictions:
            by_field[field_name].append(name)
        changes |= by_field
    if changed("patterns") or unresolved.patterns != before.unresolved.patterns:
        changes["door_type"] = [
            *_names(after.get("patterns"), options, "approved_patterns"),
            *unresolved.patterns,
        ]
    return BuildEditPatch.from_attributes(changes)


def _blank_as_none(value: JSONValue) -> JSONValue:
    return None if isinstance(value, str) and not value.strip() else value


def _strings(value: JSONValue) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _dimensions(answers: Mapping[str, JSONValue], *field_ids: str) -> tuple[int | None, ...]:
    return tuple(cast(int | None, answers.get(field_id)) for field_id in field_ids)


def _names(keys: JSONValue, options: CatalogueOptions, source: str) -> list[str]:
    labels = {choice.value: choice.label for choice in options.get(source, ())}
    return [labels[key] for key in _strings(keys) if key in labels]


__all__ = ["BuildAnswers", "CatalogueOptions", "answers_from_build", "patch_from_answers"]

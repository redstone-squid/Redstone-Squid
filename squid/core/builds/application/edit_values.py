"""Canonical snapshots and patch encoding for build edits."""

import json

from squid.core.builds.application.editing import BuildEditPatch
from squid.core.builds.domain import Info, ShowcaseTag, UnresolvedTags
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.edits.domain import EditValues
from squid.core.errors import JSONValue
from squid.core.sentinel import UNSET
from squid.core.tags.domain import TagSemanticKind, assignment_value


def _patch_document(patch: BuildEditPatch) -> dict[str, object]:
    values = patch.attributes()
    if patch.credits is not UNSET:
        values["credits"] = [credit.to_json() for credit in patch.credits]
    if patch.showcase_tags is not UNSET:
        values["showcase_tags"] = [tag.to_json() for tag in patch.showcase_tags]
    if isinstance(patch.door_expansion, DoorExpansion):
        values["door_expansion"] = patch.door_expansion.model_dump(mode="json")
    return values


def patch_values(patch: BuildEditPatch) -> EditValues:
    return EditValues(json.dumps(_patch_document(patch)))


def build_values(build: Build) -> EditValues:
    """Capture editable facts without derived values or submission origin."""
    patch = BuildEditPatch(
        display_name=build.display_name,
        version_spec=build.version_spec,
        versions=list(build.versions),
        showcase_tags=[
            ShowcaseTag(assignment.definition.stable_key, assignment_value(assignment.value))
            for assignment in build.tags
            if assignment.definition.semantic_kind is TagSemanticKind.SHOWCASE
        ],
        dimensions=build.dimensions,
        door_type=list(build.patterns),
        wiring_placement_restrictions=list(build.wiring_placement_restrictions),
        animated_restrictions=list(build.animated_restrictions),
        component_restrictions=list(build.component_restrictions),
        miscellaneous_restrictions=list(build.miscellaneous_restrictions),
        extra_user_info=build.description,
        credits=list(build.credits),
        image_urls=list(build.image_urls),
        video_urls=list(build.video_urls),
        world_download_urls=list(build.world_download_urls),
        schematic_urls=list(build.schematic_urls),
        render_urls=list(build.render_urls),
        server_ip=build.server_address,
        coordinates=build.server_coordinates,
        command_to_get_to_build=build.server_command,
        completion_time=build.completion_time,
    )
    if isinstance(build, DoorBuild):
        patch = BuildEditPatch.combine(
            (
                patch,
                BuildEditPatch(
                    door_dimensions=build.door_dimensions,
                    # Without this an expansion-only edit compares equal before
                    # and after, is skipped, and cannot be undone.
                    door_expansion=build.expansion,
                    door_orientation_type=build.orientation,
                    visible_opening_time=build.visible_opening_time,
                    visible_closing_time=build.visible_closing_time,
                    normal_opening_time=build.normal_opening_time,
                    normal_closing_time=build.normal_closing_time,
                ),
            )
        )
    if isinstance(build, ExtenderBuild):
        patch = BuildEditPatch.combine(
            (
                patch,
                BuildEditPatch(
                    extender_orientation=build.orientation,
                    extension_length=build.extension_length,
                    extension_time=build.extension_time,
                    retraction_time=build.retraction_time,
                ),
            )
        )
    unresolved: Info = {}
    UnresolvedTags.read(build.extra_info).write(unresolved)
    return EditValues(json.dumps(_patch_document(patch) | {"unresolved_tags": unresolved}))


def changed_build_values(before: EditValues, after: EditValues) -> tuple[tuple[str, JSONValue, JSONValue], ...]:
    """Return a stable diff suitable for authorization and review."""
    previous, proposed = before.unpack(), after.unpack()
    return tuple(
        (key, previous.get(key), proposed.get(key))
        for key in sorted(previous.keys() | proposed.keys())
        if previous.get(key) != proposed.get(key)
    )

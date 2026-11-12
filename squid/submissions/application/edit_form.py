"""The form used to edit a saved build, derived from the current submission form.

Drafts and saved builds are edited with the same Discord editor, so a build should read the
same in both: same sections, same labels, same catalogue pickers. This module builds the edit
form from the current submission manifest rather than defining a second list of fields.

The derivation:

- drops fields that only describe a new submission (schematic sharing and licence, the rights
  attestation, AI-generated, sponsor attribution, and tag proposals) because a saved build
  either keeps them only as provenance or does not allow editing them;
- replaces the single source version a submission asks for with the build's full version list;
- relaxes limits to those of the shared build patch validation (`BuildPatch`), so opening a
  build that was saved under the larger limits never truncates it;
- adds a "Links and location" section for media links and server details, which have no
  submission field.

The result is never stored and never published to submission clients; drafts keep using the
pinned submission manifest.
"""

from dataclasses import replace

from squid.core.i18n import localization_for, tr
from squid.submissions.application.forms import build_submission_manifest
from squid.submissions.domain import (
    ControlKind,
    FieldConstraints,
    FormField,
    FormManifest,
    FormSection,
    ValueKind,
)
from squid_i18n import Message, localization_scope

BUILD_EDIT_SCHEMA = "build_edit"

SUBMISSION_ONLY_FIELDS = frozenset(
    {
        "schematic_visibility",
        "schematic_license",
        "rights_attestation",
        "ai_generated",
        "sponsor_attribution",
        "restriction_proposals",
        "pattern_proposals",
        "source_version",
    }
)
"""Submission fields a saved build cannot change; see the module docstring."""

# Limits from BuildPatch (squid/builds/application/edit_schema.py), so a saved build always fits.
_EDIT_CONSTRAINTS = {
    "description": FieldConstraints(max_length=10_000),
    "creators": FieldConstraints(min_length=1, max_length=120, max_items=100),
    "restrictions": FieldConstraints(max_length=64, max_items=400),
    "patterns": FieldConstraints(max_length=64, max_items=50),
    "completion": FieldConstraints(max_length=100),
}


def build_edit_form(locale: str | None = None) -> FormManifest:
    """Return the edit form for saved builds, with text resolved in `locale`."""
    submission = build_submission_manifest(locale)
    with localization_scope(localization_for(locale)):
        common = tuple(
            section for section in (_edit_section(section) for section in submission.common_sections) if section.fields
        )
        return FormManifest(
            schema_id=BUILD_EDIT_SCHEMA,
            revision=submission.revision,
            minimum_protocol=submission.minimum_protocol,
            maximum_protocol=submission.maximum_protocol,
            common_sections=(*common, _links_and_location()),
            categories=tuple(
                replace(
                    category,
                    sections=tuple(
                        section
                        for section in (_edit_section(section) for section in category.sections)
                        if section.fields
                    ),
                )
                for category in submission.categories
            ),
        )


def _edit_section(section: FormSection) -> FormSection:
    fields: list[FormField] = []
    for field in section.fields:
        if field.id == "source_version":
            fields.append(_versions_field(field))
        if field.id in SUBMISSION_ONLY_FIELDS:
            continue
        if field.id == "creators":
            # Older builds can have no creators; requiring one would block every other edit.
            field = replace(field, required=False)
        if (constraints := _EDIT_CONSTRAINTS.get(field.id)) is not None:
            field = replace(field, constraints=constraints)
        fields.append(field)
    if section.id == "submission_context":
        # Only the completion date is left, and it describes the build rather than its submission.
        return replace(
            section, title=Message.resolve(tr(t"build-edit-form-about = About this build")), fields=tuple(fields)
        )
    return replace(section, fields=tuple(fields))


def _versions_field(source_version: FormField) -> FormField:
    """A saved build lists every version it was recorded for, not one source version."""
    return FormField(
        id="versions",
        label=Message.resolve(tr(t"build-edit-form-versions = Minecraft versions")),
        control=ControlKind.MULTI_CHOICE,
        value_kind=ValueKind.STRING_LIST,
        option_source=source_version.option_source,
        constraints=FieldConstraints(max_length=80, max_items=100),
    )


def _links_and_location() -> FormSection:
    def links(field_id: str, label: Message) -> FormField:
        return FormField(
            id=field_id,
            label=Message.resolve(label),
            help_text=Message.resolve(tr(t"build-edit-form-one-per-line = One link per line.")),
            control=ControlKind.TEXT,
            value_kind=ValueKind.STRING_LIST,
            repeatable=True,
            constraints=FieldConstraints(max_items=100),
            required_capability="repeatable_text",
        )

    def text(field_id: str, label: Message, max_length: int) -> FormField:
        return FormField(
            id=field_id,
            label=Message.resolve(label),
            control=ControlKind.TEXT,
            value_kind=ValueKind.STRING,
            constraints=FieldConstraints(max_length=max_length),
        )

    return FormSection(
        id="links_location",
        title=Message.resolve(tr(t"build-edit-form-links-location = Links and location")),
        fields=(
            links("image_urls", tr(t"build-edit-form-images = Image links")),
            links("video_urls", tr(t"build-edit-form-videos = Video links")),
            links("world_download_urls", tr(t"build-edit-form-world-downloads = World download links")),
            text("server_ip", tr(t"build-edit-form-server = Server address"), 500),
            text("coordinates", tr(t"build-edit-form-coordinates = Coordinates"), 500),
            text("command_to_get_to_build", tr(t"build-edit-form-command = Command to get there"), 2_000),
        ),
    )


__all__ = ["BUILD_EDIT_SCHEMA", "SUBMISSION_ONLY_FIELDS", "build_edit_form"]

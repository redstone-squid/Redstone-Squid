"""Authored submission form schema and dynamic option resolution."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from squid.core.i18n import _, translate
from squid.submissions.domain import (
    CategoryForm,
    ChoiceOption,
    ControlKind,
    FieldConstraints,
    FormField,
    FormManifest,
    FormSection,
    SubmissionOrigin,
    ValueKind,
    VisibilityOperator,
    VisibilityRule,
)

CURRENT_SUBMISSION_PROTOCOL = 1
CURRENT_SUBMISSION_SCHEMA = "build_submission.v1"
CURRENT_SUBMISSION_SCHEMA_REVISION = 1


class FormOptionCatalog(Protocol):
    """Resolve approved, revisioned options without coupling the form to persistence."""

    async def options(
        self,
        source: str,
        category: str,
        *,
        locale: str | None,
    ) -> "FormOptionSet": ...


@dataclass(frozen=True, slots=True)
class FormOptionSet:
    """One revision of an approved dynamic option source."""

    source: str
    category: str
    revision: int
    options: tuple[ChoiceOption, ...]

    def __post_init__(self) -> None:
        if self.revision < 1:
            msg = "option revisions must be positive"
            raise ValueError(msg)


class SubmissionFormService:
    """Publish the fixed form definition with current approved dynamic options."""

    def __init__(self, option_catalog: FormOptionCatalog) -> None:
        self._option_catalog = option_catalog

    def manifest(self, *, locale: str | None) -> FormManifest:
        """Return the localized current form with revisioned option-source references."""
        return build_submission_manifest(locale)

    async def manifest_revision(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None:
        """Return one immutable checked-in form revision while this server can validate it."""
        return await CheckedInFormManifestRegistry().get(schema_id, revision, locale=locale)

    async def options(
        self,
        source: str,
        category: str,
        *,
        locale: str | None,
    ) -> FormOptionSet:
        """Resolve one category-aware approved option source."""
        return await self._option_catalog.options(source, category, locale=locale)


class CheckedInFormManifestRegistry:
    """Resolve the current checked-in schema and reject revisions this binary cannot serve."""

    async def current(self, *, locale: str | None) -> FormManifest:
        """Return the only schema revision authored by this binary."""
        return build_submission_manifest(locale)

    async def get(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None:
        """Return a pinned schema when its exact immutable revision is still available."""
        if schema_id != CURRENT_SUBMISSION_SCHEMA or revision != CURRENT_SUBMISSION_SCHEMA_REVISION:
            return None
        return build_submission_manifest(locale)


def build_submission_manifest(locale: str | None = None) -> FormManifest:
    """Build the checked-in schema revision localized for one client."""
    localize = lambda message: translate(locale, message)
    common = (
        FormSection(
            id="identity",
            title=localize(_("Build identity")),
            fields=(
                FormField(
                    id="display_name",
                    label=localize(_("Display name")),
                    help_text=localize(_("Optional name shown in addition to the canonical structured title.")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=120),
                ),
                FormField(
                    id="description",
                    label=localize(_("Description")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=4_000),
                ),
                FormField(
                    id="creators",
                    label=localize(_("Creators")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING_LIST,
                    required=True,
                    repeatable=True,
                    constraints=FieldConstraints(min_length=1, max_length=120, min_items=1, max_items=20),
                    required_capability="repeatable_text",
                ),
            ),
        ),
        FormSection(
            id="dimensions_versions",
            title=localize(_("Dimensions and versions")),
            fields=(
                _integer_field("capture_width", _("Build width"), localize, required=True, maximum=512),
                _integer_field("capture_height", _("Build height"), localize, required=True, maximum=512),
                _integer_field("capture_depth", _("Build depth"), localize, required=True, maximum=512),
                FormField(
                    id="source_version",
                    label=localize(_("Exact source Minecraft version")),
                    control=ControlKind.CHOICE,
                    value_kind=ValueKind.STRING,
                    required=True,
                    option_source="approved_source_versions",
                    constraints=FieldConstraints(min_length=1, max_length=80),
                ),
                FormField(
                    id="version_compatibility",
                    label=localize(_("Declared version compatibility")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=500),
                ),
            ),
        ),
        FormSection(
            id="taxonomy",
            title=localize(_("Restrictions and tags")),
            fields=(
                FormField(
                    id="restrictions",
                    label=localize(_("Known restrictions")),
                    control=ControlKind.MULTI_CHOICE,
                    value_kind=ValueKind.STRING_LIST,
                    option_source="approved_restrictions",
                    constraints=FieldConstraints(max_length=64, max_items=40),
                ),
                FormField(
                    id="restriction_proposals",
                    label=localize(_("Propose missing restrictions")),
                    help_text=localize(_("Suggestions remain unofficial until staff promotes them.")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING_LIST,
                    repeatable=True,
                    constraints=FieldConstraints(max_length=200, max_items=5),
                    required_capability="repeatable_text",
                ),
                FormField(
                    id="showcase_tags",
                    label=localize(_("Showcase tags")),
                    control=ControlKind.MULTI_CHOICE,
                    value_kind=ValueKind.STRING_LIST,
                    option_source="approved_showcase_tags",
                    constraints=FieldConstraints(max_length=64, max_items=40),
                ),
            ),
        ),
        FormSection(
            id="rights_privacy",
            title=localize(_("Schematic rights and privacy")),
            fields=(
                FormField(
                    id="schematic_visibility",
                    label=localize(_("Schematic visibility")),
                    help_text=localize(_("Choose explicitly; publication is never inferred.")),
                    control=ControlKind.CHOICE,
                    value_kind=ValueKind.STRING,
                    required=True,
                    options=(
                        ChoiceOption("reviewer_only", localize(_("Reviewer only"))),
                        ChoiceOption("public_download", localize(_("Public download"))),
                    ),
                ),
                FormField(
                    id="schematic_license",
                    label=localize(_("Public schematic license")),
                    control=ControlKind.CHOICE,
                    value_kind=ValueKind.STRING,
                    required=True,
                    options=_license_options(localize),
                    visible_when=VisibilityRule("schematic_visibility", VisibilityOperator.EQUALS, "public_download"),
                ),
                FormField(
                    id="rights_attestation",
                    label=localize(_("I have permission to distribute this schematic under the selected license.")),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    required=True,
                    constraints=FieldConstraints(must_equal=True),
                    visible_when=VisibilityRule("schematic_visibility", VisibilityOperator.EQUALS, "public_download"),
                ),
                FormField(
                    id="include_inventories",
                    label=localize(_("Include inventories and functional item contents")),
                    help_text=localize(_("Review the disclosure preview before submitting.")),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    default=True,
                ),
                FormField(
                    id="include_free_text",
                    label=localize(_("Include signs, books, names, and other free text")),
                    help_text=localize(_("Review the disclosure preview before submitting.")),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    default=True,
                ),
            ),
        ),
        FormSection(
            id="provenance",
            title=localize(_("Provenance")),
            fields=(
                FormField(
                    id="completion",
                    label=localize(_("Completion date or context")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=200),
                ),
                FormField(
                    id="ai_generated",
                    label=localize(_("AI-generated or AI-assisted")),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    required=True,
                    default=False,
                ),
                FormField(
                    id="sponsor_attribution",
                    label=localize(_("Show the sponsoring server on this build")),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    default=False,
                    origins=frozenset({SubmissionOrigin.PAPER}),
                ),
            ),
        ),
    )
    return FormManifest(
        schema_id=CURRENT_SUBMISSION_SCHEMA,
        revision=CURRENT_SUBMISSION_SCHEMA_REVISION,
        minimum_protocol=CURRENT_SUBMISSION_PROTOCOL,
        maximum_protocol=CURRENT_SUBMISSION_PROTOCOL,
        common_sections=common,
        categories=(
            _door_form(localize),
            _extender_form(localize),
            CategoryForm("utility", localize(_("Utility")), ()),
            CategoryForm("entrance", localize(_("Entrance")), ()),
            CategoryForm("other", localize(_("Other")), ()),
        ),
    )


def _door_form(localize: Callable[[str], str]) -> CategoryForm:
    return CategoryForm(
        code="door",
        label=localize(_("Door")),
        sections=(
            FormSection(
                id="door_geometry",
                title=localize(_("Door geometry")),
                fields=(
                    _integer_field("opening_width", _("Opening width"), localize, required=True, maximum=512),
                    _integer_field("opening_height", _("Opening height"), localize, required=True, maximum=512),
                    _integer_field("opening_depth", _("Opening depth"), localize, required=True, maximum=512),
                    FormField(
                        id="door_orientation",
                        label=localize(_("Door orientation")),
                        control=ControlKind.CHOICE,
                        value_kind=ValueKind.STRING,
                        required=True,
                        options=(
                            ChoiceOption("door", localize(_("Door"))),
                            ChoiceOption("skydoor", localize(_("Skydoor"))),
                            ChoiceOption("trapdoor", localize(_("Trapdoor"))),
                        ),
                    ),
                ),
            ),
            _pattern_section(localize),
            FormSection(
                id="door_timing",
                title=localize(_("Optional default timing")),
                fields=(
                    _duration_field("opening_time", _("Opening time"), localize),
                    _duration_field("visible_opening_time", _("Visible opening time"), localize),
                    _duration_field("closing_time", _("Closing time"), localize),
                    _duration_field("visible_closing_time", _("Visible closing time"), localize),
                ),
            ),
        ),
    )


def _extender_form(localize: Callable[[str], str]) -> CategoryForm:
    return CategoryForm(
        code="extender",
        label=localize(_("Extender")),
        sections=(
            FormSection(
                id="extender_geometry",
                title=localize(_("Extender movement")),
                fields=(
                    FormField(
                        id="movement_orientation",
                        label=localize(_("Movement orientation")),
                        control=ControlKind.CHOICE,
                        value_kind=ValueKind.STRING,
                        required=True,
                        options=(
                            ChoiceOption("horizontal", localize(_("Horizontal"))),
                            ChoiceOption("vertical_up", localize(_("Vertical upward"))),
                            ChoiceOption("vertical_down", localize(_("Vertical downward"))),
                        ),
                    ),
                    _integer_field("extension_length", _("Extension length"), localize, required=True, maximum=512),
                ),
            ),
            _pattern_section(localize),
            FormSection(
                id="extender_timing",
                title=localize(_("Optional default timing")),
                fields=(
                    _duration_field("extension_time", _("Extension time"), localize),
                    _duration_field("retraction_time", _("Retraction time"), localize),
                ),
            ),
        ),
    )


def _pattern_section(localize: Callable[[str], str]) -> FormSection:
    return FormSection(
        id="patterns",
        title=localize(_("Mechanism patterns")),
        fields=(
            FormField(
                id="patterns",
                label=localize(_("Known patterns")),
                control=ControlKind.MULTI_CHOICE,
                value_kind=ValueKind.STRING_LIST,
                option_source="approved_patterns",
                constraints=FieldConstraints(max_length=64, max_items=20),
            ),
            FormField(
                id="pattern_proposals",
                label=localize(_("Propose missing patterns")),
                help_text=localize(_("Suggestions remain unofficial until staff promotes them.")),
                control=ControlKind.TEXT,
                value_kind=ValueKind.STRING_LIST,
                repeatable=True,
                constraints=FieldConstraints(max_length=200, max_items=5),
                required_capability="repeatable_text",
            ),
        ),
    )


def _integer_field(
    field_id: str,
    label: str,
    localize: Callable[[str], str],
    *,
    required: bool,
    maximum: int,
) -> FormField:
    return FormField(
        id=field_id,
        label=localize(label),
        control=ControlKind.NUMBER,
        value_kind=ValueKind.INTEGER,
        required=required,
        constraints=FieldConstraints(minimum=1, maximum=maximum),
    )


def _duration_field(field_id: str, label: str, localize: Callable[[str], str]) -> FormField:
    return FormField(
        id=field_id,
        label=localize(label),
        help_text=localize(_("Enter game ticks, redstone ticks, or seconds explicitly.")),
        control=ControlKind.DURATION,
        value_kind=ValueKind.GAME_TICKS,
        constraints=FieldConstraints(minimum=0),
    )


def _license_options(localize: Callable[[str], str]) -> tuple[ChoiceOption, ...]:
    return tuple(
        ChoiceOption(value, localize(label))
        for value, label in (
            ("cc0_1_0", _("CC0 1.0")),
            ("cc_by_4_0", _("CC BY 4.0")),
            ("cc_by_sa_4_0", _("CC BY-SA 4.0")),
            ("cc_by_nd_4_0", _("CC BY-ND 4.0")),
            ("cc_by_nc_4_0", _("CC BY-NC 4.0")),
            ("cc_by_nc_sa_4_0", _("CC BY-NC-SA 4.0")),
            ("cc_by_nc_nd_4_0", _("CC BY-NC-ND 4.0")),
        )
    )

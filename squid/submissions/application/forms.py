"""The authored build form, and dynamic option resolution for its catalogue fields.

One manifest describes every build field for both tasks that use it: submitting a new build and
editing a saved one. Each field says which tasks show it (`FormPurpose`), and a field can relax its
rules when editing (`EditRules`), because a saved build may hold values the submission rules no
longer allow. Callers never see the combined form: `build_submission_manifest` returns the submit
projection that drafts pin and the HTTP API publishes, and `build_edit_manifest` returns the
projection the Discord build editor uses.

Checked-in revisions are immutable for submissions. Edit-only fields and edit rules do not
appear in the submit projection, so adding them does not change a pinned revision.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import localization_for, tr
from squid.submissions.domain import (
    CategoryForm,
    ChoiceOption,
    ControlKind,
    EditRules,
    FieldConstraints,
    FormField,
    FormManifest,
    FormPurpose,
    FormSection,
    SubmissionOrigin,
    ValueKind,
    VisibilityOperator,
    VisibilityRule,
)
from squid_i18n import Message, localization_scope

CURRENT_SUBMISSION_PROTOCOL = 1
CURRENT_SUBMISSION_SCHEMA = "build_submission.v1"
# New drafts use revision 4. Revisions 2 and 3 stay readable for existing drafts, which can be
# upgraded one revision at a time. Revision 4 relaxes sizes (build size optional, door depth
# optional) and stops asking Discord submitters for the exported Minecraft version.
CURRENT_SUBMISSION_SCHEMA_REVISION = 4
_CHECKED_IN_REVISIONS = frozenset({2, 3, 4})


class FormManifestRegistry(Protocol):
    """Resolve immutable checked-in form revisions."""

    async def current(self, *, locale: str | None) -> FormManifest: ...

    async def get(self, schema_id: str, revision: int, *, locale: str | None) -> FormManifest | None: ...

    async def edit(self, *, locale: str | None) -> FormManifest: ...


class FormOptionCatalog(Protocol):
    """Resolve approved, revisioned options without coupling the form to persistence."""

    async def options(
        self,
        source: str,
        category: str,
        *,
        locale: str | None,
    ) -> FormOptionSet: ...


@dataclass(frozen=True, slots=True)
class FormOptionSet:
    """One revision of an approved dynamic option source."""

    source: str
    category: str
    revision: int
    options: tuple[ChoiceOption, ...]

    def __post_init__(self) -> None:
        if self.revision < 1:
            msg = tr(t"submissions-option-revisions-positive = option revisions must be positive")
            raise InvalidStateError(msg)


class SubmissionFormService:
    """Serve form definitions and the current choices for dynamic fields.

    A manifest revision fixes the fields, validation rules, and inline choices.
    Fields with option_source instead name a separately fetched option set,
    such as approved tags. Those choices have their own revision and can change
    without changing the draft's form revision.
    """

    def __init__(self, option_catalog: FormOptionCatalog, manifests: FormManifestRegistry) -> None:
        self._option_catalog = option_catalog
        self._manifests = manifests

    async def manifest(self, *, locale: str | None) -> FormManifest:
        """Return the localized current form with revisioned option-source references."""
        return await self._manifests.current(locale=locale)

    async def manifest_revision(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None:
        """Return one immutable checked-in form revision while this server can validate it."""
        return await self._manifests.get(schema_id, revision, locale=locale)

    async def edit_manifest(self, *, locale: str | None) -> FormManifest:
        """Return the localized current form for editing saved builds; see `build_edit_manifest`."""
        return await self._manifests.edit(locale=locale)

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
        """Return the revision selected for newly created drafts."""
        return build_submission_manifest(locale)

    async def get(
        self,
        schema_id: str,
        revision: int,
        *,
        locale: str | None,
    ) -> FormManifest | None:
        """Return a pinned schema when its exact immutable revision is still available."""
        if schema_id != CURRENT_SUBMISSION_SCHEMA or revision not in _CHECKED_IN_REVISIONS:
            return None
        return build_submission_manifest(locale, revision=revision)

    async def edit(self, *, locale: str | None) -> FormManifest:
        """Return the current form for editing saved builds."""
        return build_edit_manifest(locale)


def build_submission_manifest(
    locale: str | None = None,
    *,
    revision: int = CURRENT_SUBMISSION_SCHEMA_REVISION,
) -> FormManifest:
    """Build the checked-in schema revision localized for one client."""
    if revision not in _CHECKED_IN_REVISIONS:
        msg = tr(t"submissions-unsupported-checked-submission = unsupported checked-in submission form revision")
        raise InvalidStateError(msg)
    with localization_scope(localization_for(locale)):
        return _build_form_manifest(revision).for_purpose(FormPurpose.SUBMIT)


def build_edit_manifest(locale: str | None = None) -> FormManifest:
    """Build the current form for editing saved builds, localized for one client."""
    with localization_scope(localization_for(locale)):
        return _build_form_manifest(CURRENT_SUBMISSION_SCHEMA_REVISION).for_purpose(FormPurpose.EDIT)


_SUBMIT_ONLY = frozenset({FormPurpose.SUBMIT})
_EDIT_ONLY = frozenset({FormPurpose.EDIT})


def _build_form_manifest(revision: int) -> FormManifest:
    # The manifest is JSON for web clients, so its text is resolved in the requested locale here.
    localize = Message.resolve
    context_section_id = "provenance" if revision == 2 else "submission_context"
    context_title = (
        tr(t"submissions-application-forms-provenance = About this submission")
        if revision == 2
        else tr(t"submissions-about-submission = About this submission")
    )
    sponsor_label = (
        tr(t"submissions-show-sponsoring-server = Show the sponsoring server on this build")
        if revision == 2
        else tr(t"submissions-credit-minecraft-server = Credit the Minecraft server this submission came from")
    )
    common = (
        FormSection(
            id="identity",
            title=localize(tr(t"submissions-application-forms-build-identity = Name and creators")),
            fields=(
                FormField(
                    id="display_name",
                    label=localize(tr(t"submissions-application-forms-display-name = Build name")),
                    help_text=localize(
                        tr(
                            t"submissions-optional-name-shown = Optional. "
                            t"Shown next to the title generated from the build's details."
                        )
                    ),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=120),
                ),
                FormField(
                    id="description",
                    label=localize(tr(t"submissions-application-forms-description = Description")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=4_000),
                    edit_rules=EditRules(constraints=FieldConstraints(max_length=10_000)),
                ),
                FormField(
                    id="creators",
                    label=localize(tr(t"submissions-application-forms-creators = Creators")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING_LIST,
                    required=True,
                    repeatable=True,
                    constraints=FieldConstraints(min_length=1, max_length=120, min_items=1, max_items=20),
                    required_capability="repeatable_text",
                    # Older saved builds can have no creators; requiring one would block every other edit.
                    edit_rules=EditRules(
                        required=False, constraints=FieldConstraints(min_length=1, max_length=120, max_items=100)
                    ),
                ),
            ),
        ),
        FormSection(
            id="dimensions_versions",
            title=localize(tr(t"submissions-dimensions-versions = Size and version")),
            fields=(
                _integer_field(
                    "capture_width",
                    tr(t"submissions-application-forms-build-width = Build width"),
                    localize,
                    required=revision < 4,
                    maximum=512,
                ),
                _integer_field(
                    "capture_height",
                    tr(t"submissions-application-forms-build-height = Build height"),
                    localize,
                    required=revision < 4,
                    maximum=512,
                ),
                _integer_field(
                    "capture_depth",
                    tr(t"submissions-application-forms-build-depth = Build depth"),
                    localize,
                    required=revision < 4,
                    maximum=512,
                ),
                FormField(
                    id="source_version",
                    label=localize(tr(t"submissions-exact-source-minecraft = Minecraft version")),
                    help_text=(
                        localize(
                            tr(
                                t"submissions-source-version-help = "
                                t"The version the schematic or world capture was exported from."
                            )
                        )
                        if revision >= 4
                        else None
                    ),
                    control=ControlKind.CHOICE,
                    value_kind=ValueKind.STRING,
                    required=True,
                    option_source="approved_source_versions",
                    constraints=FieldConstraints(min_length=1, max_length=80),
                    # Only in-game capture clients know the exported version; Discord submitters are not asked.
                    origins=(
                        frozenset(SubmissionOrigin) - {SubmissionOrigin.DISCORD}
                        if revision >= 4
                        else frozenset(SubmissionOrigin)
                    ),
                    purposes=_SUBMIT_ONLY,
                ),
                # A saved build lists every version it was recorded for, not the one it was exported from.
                FormField(
                    id="versions",
                    label=localize(tr(t"build-edit-form-versions = Minecraft versions")),
                    control=ControlKind.MULTI_CHOICE,
                    value_kind=ValueKind.STRING_LIST,
                    option_source="approved_source_versions",
                    constraints=FieldConstraints(max_length=80, max_items=100),
                    purposes=_EDIT_ONLY,
                ),
                FormField(
                    id="version_compatibility",
                    label=localize(tr(t"submissions-declared-version-compatibility = Works in versions")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=500),
                ),
            ),
        ),
        FormSection(
            id="taxonomy",
            title=localize(tr(t"submissions-restrictions-tags = Restrictions and tags")),
            fields=(
                FormField(
                    id="restrictions",
                    label=localize(tr(t"submissions-application-forms-known-restrictions = Restrictions")),
                    control=ControlKind.MULTI_CHOICE,
                    value_kind=ValueKind.STRING_LIST,
                    option_source="approved_restrictions",
                    constraints=FieldConstraints(max_length=64, max_items=40),
                    edit_rules=EditRules(constraints=FieldConstraints(max_length=64, max_items=400)),
                ),
                FormField(
                    id="restriction_proposals",
                    label=localize(tr(t"submissions-propose-missing-restrictions = Other restrictions")),
                    help_text=localize(
                        tr(
                            t"submissions-suggestions-unofficial "
                            t"= Not in the list? Add them here, one per line. Staff review new ones."
                        )
                    ),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING_LIST,
                    repeatable=True,
                    constraints=FieldConstraints(max_length=200, max_items=5),
                    required_capability="repeatable_text",
                    # A saved build's unmatched names are listed by the editor instead.
                    purposes=_SUBMIT_ONLY,
                ),
                FormField(
                    id="showcase_tags",
                    label=localize(tr(t"submissions-application-forms-showcase-tags = Tags")),
                    control=ControlKind.MULTI_CHOICE,
                    value_kind=ValueKind.STRING_LIST,
                    option_source="approved_showcase_tags",
                    constraints=FieldConstraints(max_length=64, max_items=40),
                ),
            ),
        ),
        FormSection(
            id="rights_privacy",
            title=localize(tr(t"submissions-schematic-rights-privacy = Schematic sharing")),
            fields=(
                FormField(
                    id="schematic_visibility",
                    label=localize(
                        tr(t"submissions-application-forms-schematic-visibility = Who can download the schematic")
                    ),
                    help_text=localize(
                        tr(
                            t"submissions-choose-explicitly-publication = "
                            t"Nothing is published unless you choose Anyone."
                        )
                    ),
                    control=ControlKind.CHOICE,
                    value_kind=ValueKind.STRING,
                    required=True,
                    purposes=_SUBMIT_ONLY,
                    options=(
                        ChoiceOption(
                            "reviewer_only",
                            localize(tr(t"submissions-application-forms-reviewer-only = Staff reviewers only")),
                        ),
                        ChoiceOption(
                            "public_download",
                            localize(tr(t"submissions-application-forms-public-download = Anyone")),
                        ),
                    ),
                ),
                FormField(
                    id="schematic_license",
                    label=localize(tr(t"submissions-public-schematic-license = Schematic license")),
                    control=ControlKind.CHOICE,
                    value_kind=ValueKind.STRING,
                    required=True,
                    options=_license_options(localize),
                    visible_when=VisibilityRule("schematic_visibility", VisibilityOperator.EQUALS, "public_download"),
                    purposes=_SUBMIT_ONLY,
                ),
                FormField(
                    id="rights_attestation",
                    label=localize(
                        tr(
                            t"submissions-i-permission-distribute = I "
                            t"have permission to distribute this schematic under the selected license."
                        )
                    ),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    required=True,
                    constraints=FieldConstraints(must_equal=True),
                    visible_when=VisibilityRule("schematic_visibility", VisibilityOperator.EQUALS, "public_download"),
                    purposes=_SUBMIT_ONLY,
                ),
            ),
        ),
        FormSection(
            id=context_section_id,
            title=localize(context_title),
            # Editing leaves only the completion date, which describes the build, not its submission.
            edit_title=localize(tr(t"build-edit-form-about = About this build")),
            fields=(
                FormField(
                    id="completion",
                    label=localize(tr(t"submissions-completion-date-context = When it was finished")),
                    control=ControlKind.TEXT,
                    value_kind=ValueKind.STRING,
                    constraints=FieldConstraints(max_length=200),
                    edit_rules=EditRules(constraints=FieldConstraints(max_length=100)),
                ),
                FormField(
                    id="ai_generated",
                    label=localize(tr(t"submissions-ai-generated-ai = AI-generated or AI-assisted")),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    required=True,
                    default=False,
                    # Stored on the build, but the shared build patch does not allow changing it.
                    purposes=_SUBMIT_ONLY,
                ),
                FormField(
                    id="sponsor_attribution",
                    label=localize(sponsor_label),
                    control=ControlKind.BOOLEAN,
                    value_kind=ValueKind.BOOLEAN,
                    default=False,
                    origins=frozenset({SubmissionOrigin.PAPER}),
                    purposes=_SUBMIT_ONLY,
                ),
            ),
        ),
        _links_and_location(localize),
    )
    return FormManifest(
        schema_id=CURRENT_SUBMISSION_SCHEMA,
        revision=revision,
        minimum_protocol=CURRENT_SUBMISSION_PROTOCOL,
        maximum_protocol=CURRENT_SUBMISSION_PROTOCOL,
        common_sections=common,
        categories=(
            _door_form(localize, revision),
            _extender_form(localize),
            CategoryForm("utility", localize(tr(t"submissions-application-forms-utility = Utility")), ()),
            CategoryForm("entrance", localize(tr(t"submissions-application-forms-entrance = Entrance")), ()),
            CategoryForm("other", localize(tr(t"submissions-application-forms-other = Other")), ()),
        ),
    )


def _door_form(localize: Callable[[Message], str], revision: int = CURRENT_SUBMISSION_SCHEMA_REVISION) -> CategoryForm:
    opening_width = (
        tr(t"submissions-application-forms-opening-width = Opening width")
        if revision == 2
        else tr(t"submissions-clear-opening-width = Clear opening width")
    )
    opening_height = (
        tr(t"submissions-application-forms-opening-height = Opening height")
        if revision == 2
        else tr(t"submissions-clear-opening-height = Clear opening height")
    )
    opening_depth = (
        tr(t"submissions-application-forms-opening-depth = Opening depth")
        if revision == 2
        else tr(t"submissions-clear-opening-depth = Clear opening depth")
    )
    return CategoryForm(
        code="door",
        label=localize(tr(t"submissions-application-forms-door = Door")),
        sections=(
            FormSection(
                id="door_geometry",
                title=localize(tr(t"submissions-application-forms-door-geometry = Door size and type")),
                fields=(
                    _integer_field(
                        "opening_width",
                        opening_width,
                        localize,
                        required=True,
                        maximum=512,
                    ),
                    _integer_field(
                        "opening_height",
                        opening_height,
                        localize,
                        required=True,
                        maximum=512,
                    ),
                    _integer_field(
                        "opening_depth",
                        opening_depth,
                        localize,
                        required=revision < 4,
                        maximum=512,
                    ),
                    FormField(
                        id="door_orientation",
                        label=localize(tr(t"submissions-application-forms-door-orientation = Door type")),
                        control=ControlKind.CHOICE,
                        value_kind=ValueKind.STRING,
                        required=True,
                        options=(
                            ChoiceOption("door", localize(tr(t"submissions-application-forms-door = Door"))),
                            ChoiceOption("skydoor", localize(tr(t"submissions-application-forms-skydoor = Skydoor"))),
                            ChoiceOption(
                                "trapdoor", localize(tr(t"submissions-application-forms-trapdoor = Trapdoor"))
                            ),
                        ),
                    ),
                ),
            ),
            _pattern_section(localize),
            FormSection(
                id="door_timing",
                title=localize(tr(t"submissions-optional-default-timing = Timings")),
                fields=(
                    _duration_field(
                        "opening_time", tr(t"submissions-application-forms-opening-time = Opening time"), localize
                    ),
                    _duration_field(
                        "visible_opening_time",
                        tr(t"submissions-visible-opening-time = Visible opening time"),
                        localize,
                    ),
                    _duration_field(
                        "closing_time", tr(t"submissions-application-forms-closing-time = Closing time"), localize
                    ),
                    _duration_field(
                        "visible_closing_time",
                        tr(t"submissions-visible-closing-time = Visible closing time"),
                        localize,
                    ),
                ),
            ),
        ),
    )


def _extender_form(localize: Callable[[Message], str]) -> CategoryForm:
    return CategoryForm(
        code="extender",
        label=localize(tr(t"submissions-application-forms-extender = Extender")),
        sections=(
            FormSection(
                id="extender_geometry",
                title=localize(tr(t"submissions-application-forms-extender-movement = Extender size and direction")),
                fields=(
                    FormField(
                        id="movement_orientation",
                        label=localize(tr(t"submissions-application-forms-movement-orientation = Direction")),
                        control=ControlKind.CHOICE,
                        value_kind=ValueKind.STRING,
                        required=True,
                        options=(
                            ChoiceOption(
                                "horizontal", localize(tr(t"submissions-application-forms-horizontal = Horizontal"))
                            ),
                            ChoiceOption(
                                "vertical_up",
                                localize(tr(t"submissions-application-forms-vertical-upward = Vertical upward")),
                            ),
                            ChoiceOption(
                                "vertical_down",
                                localize(tr(t"submissions-application-forms-vertical-downward = Vertical downward")),
                            ),
                        ),
                    ),
                    _integer_field(
                        "extension_length",
                        tr(t"submissions-application-forms-extension-length = Extension length"),
                        localize,
                        required=True,
                        maximum=512,
                    ),
                ),
            ),
            _pattern_section(localize),
            FormSection(
                id="extender_timing",
                title=localize(tr(t"submissions-optional-default-timing = Timings")),
                fields=(
                    _duration_field(
                        "extension_time", tr(t"submissions-application-forms-extension-time = Extension time"), localize
                    ),
                    _duration_field(
                        "retraction_time",
                        tr(t"submissions-application-forms-retraction-time = Retraction time"),
                        localize,
                    ),
                ),
            ),
        ),
    )


def _links_and_location(localize: Callable[[Message], str]) -> FormSection:
    """Media links and server details, which a submission collects as files and context instead."""

    def links(field_id: str, label: Message) -> FormField:
        return FormField(
            id=field_id,
            label=localize(label),
            help_text=localize(tr(t"build-edit-form-one-per-line = One link per line.")),
            control=ControlKind.TEXT,
            value_kind=ValueKind.STRING_LIST,
            repeatable=True,
            constraints=FieldConstraints(max_items=100),
            required_capability="repeatable_text",
            purposes=_EDIT_ONLY,
        )

    def text(field_id: str, label: Message, max_length: int) -> FormField:
        return FormField(
            id=field_id,
            label=localize(label),
            control=ControlKind.TEXT,
            value_kind=ValueKind.STRING,
            constraints=FieldConstraints(max_length=max_length),
            purposes=_EDIT_ONLY,
        )

    return FormSection(
        id="links_location",
        title=localize(tr(t"build-edit-form-links-location = Links and location")),
        fields=(
            links("image_urls", tr(t"build-edit-form-images = Image links")),
            links("video_urls", tr(t"build-edit-form-videos = Video links")),
            links("world_download_urls", tr(t"build-edit-form-world-downloads = World download links")),
            text("server_ip", tr(t"build-edit-form-server = Server address"), 500),
            text("coordinates", tr(t"build-edit-form-coordinates = Coordinates"), 500),
            text("command_to_get_to_build", tr(t"build-edit-form-command = Command to get there"), 2_000),
        ),
    )


def _pattern_section(localize: Callable[[Message], str]) -> FormSection:
    return FormSection(
        id="patterns",
        title=localize(tr(t"submissions-application-forms-mechanism-patterns = Patterns")),
        fields=(
            FormField(
                id="patterns",
                label=localize(tr(t"submissions-application-forms-known-patterns = Patterns")),
                control=ControlKind.MULTI_CHOICE,
                value_kind=ValueKind.STRING_LIST,
                option_source="approved_patterns",
                constraints=FieldConstraints(max_length=64, max_items=20),
                edit_rules=EditRules(constraints=FieldConstraints(max_length=64, max_items=50)),
            ),
            FormField(
                id="pattern_proposals",
                label=localize(tr(t"submissions-propose-missing-patterns = Other patterns")),
                help_text=localize(
                    tr(
                        t"submissions-suggestions-unofficial = Not in the list? Add them here, one per line. Staff review new ones."
                    )
                ),
                control=ControlKind.TEXT,
                value_kind=ValueKind.STRING_LIST,
                repeatable=True,
                constraints=FieldConstraints(max_length=200, max_items=5),
                required_capability="repeatable_text",
                purposes=_SUBMIT_ONLY,
            ),
        ),
    )


def _integer_field(
    field_id: str,
    label: Message,
    localize: Callable[[Message], str],
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


def _duration_field(field_id: str, label: Message, localize: Callable[[Message], str]) -> FormField:
    return FormField(
        id=field_id,
        label=localize(label),
        help_text=localize(tr(t"submissions-enter-game-ticks = Include a unit: 30gt, 15rt, or 1.5s.")),
        control=ControlKind.DURATION,
        value_kind=ValueKind.GAME_TICKS,
        constraints=FieldConstraints(minimum=0),
    )


def _license_options(localize: Callable[[Message], str]) -> tuple[ChoiceOption, ...]:
    return tuple(
        ChoiceOption(value, localize(label))
        for value, label in (
            ("cc0_1_0", tr(t"submissions-application-forms-cc = CC0 1.0")),
            ("cc_by_4_0", tr(t"submissions-application-forms-cc-by = CC BY 4.0")),
            ("cc_by_sa_4_0", tr(t"submissions-cc-by-sa = CC BY-SA 4.0")),
            ("cc_by_nd_4_0", tr(t"submissions-cc-by-nd = CC BY-ND 4.0")),
            ("cc_by_nc_4_0", tr(t"submissions-cc-by-nc = CC BY-NC 4.0")),
            ("cc_by_nc_sa_4_0", tr(t"submissions-cc-by-nc-sa = CC BY-NC-SA 4.0")),
            ("cc_by_nc_nd_4_0", tr(t"submissions-cc-by-nc-nd = CC BY-NC-ND 4.0")),
        )
    )

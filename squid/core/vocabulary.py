"""Display names for domain values, shared by the bot and the website.

Each value has exactly one translated label here. `scripts/export_vocabulary.py` writes every
label in every locale to `contracts/vocabulary.json`, which the website compiles into typed
functions, so adding an enum member means adding its label here.
"""

from collections.abc import Mapping

from squid.core.builds.domain.models import BuildStatus
from squid.core.i18n import tr
from squid.core.records.domain.models import BuildKind, RecordCriterion, RecordFact, VersionScope
from squid_i18n import Text

RECORD_CRITERIA: Mapping[RecordCriterion, Text] = {
    RecordCriterion.FIRST: tr(t"vocabulary-criterion-first = First"),
    RecordCriterion.FASTEST: tr(t"vocabulary-criterion-fastest = Fastest"),
    RecordCriterion.SMALLEST: tr(t"vocabulary-criterion-smallest = Smallest"),
    RecordCriterion.FASTEST_SMALLEST: tr(t"vocabulary-criterion-fastest-smallest = Fastest, then smallest"),
    RecordCriterion.SMALLEST_FASTEST: tr(t"vocabulary-criterion-smallest-fastest = Smallest, then fastest"),
}

BUILD_KINDS: Mapping[BuildKind, Text] = {
    BuildKind.DOOR: tr(t"vocabulary-build-kind-door = Door"),
    BuildKind.ENTRANCE: tr(t"vocabulary-build-kind-entrance = Entrance"),
    BuildKind.EXTENDER: tr(t"vocabulary-build-kind-extender = Piston extender"),
    BuildKind.UTILITY: tr(t"vocabulary-build-kind-utility = Utility"),
}

VERSION_SCOPES: Mapping[VersionScope, Text] = {
    VersionScope.ALL_TIME: tr(t"vocabulary-version-scope-all-time = All time"),
    VersionScope.UP_TO_DATE: tr(t"vocabulary-version-scope-up-to-date = Current version"),
}

RECORD_FACTS: Mapping[RecordFact, Text] = {
    RecordFact.COMPLETION_AT: tr(t"vocabulary-record-fact-completion = Completion date"),
    RecordFact.FIXED_VOLUME: tr(t"vocabulary-record-fact-volume = Volume"),
    RecordFact.OPENING: tr(t"vocabulary-record-fact-opening = Opening time"),
    RecordFact.OPENING_VISIBLE: tr(t"vocabulary-record-fact-opening-visible = Visible opening time"),
    RecordFact.CLOSING: tr(t"vocabulary-record-fact-closing = Closing time"),
    RecordFact.CLOSING_VISIBLE: tr(t"vocabulary-record-fact-closing-visible = Visible closing time"),
    RecordFact.OPENING_RESET: tr(t"vocabulary-record-fact-opening-reset = Opening reset time"),
    RecordFact.CLOSING_RESET: tr(t"vocabulary-record-fact-closing-reset = Closing reset time"),
    RecordFact.RETRACTION: tr(t"vocabulary-record-fact-retraction = Retraction time"),
    RecordFact.EXTENSION: tr(t"vocabulary-record-fact-extension = Extension time"),
    RecordFact.RETRACTION_RESET: tr(t"vocabulary-record-fact-retraction-reset = Retraction reset time"),
    RecordFact.EXTENSION_RESET: tr(t"vocabulary-record-fact-extension-reset = Extension reset time"),
}

BUILD_STATUSES: Mapping[BuildStatus, Text] = {
    BuildStatus.PENDING: tr(t"vocabulary-build-status-pending = Pending"),
    BuildStatus.APPROVED: tr(t"vocabulary-build-status-approved = Approved"),
    BuildStatus.REJECTED: tr(t"vocabulary-build-status-rejected = Rejected"),
}

SEARCH_BUILD_KINDS: Mapping[str, Text] = {
    **{kind.value: label for kind, label in BUILD_KINDS.items()},
    "other": tr(t"vocabulary-search-build-kind-other = Other"),
}
"""Build kinds as search filters them, where `other` matches builds of no recognized kind."""

RECORD_STATES: Mapping[str, Text] = {
    "current": tr(t"vocabulary-record-state-current = Current"),
    "retired": tr(t"vocabulary-record-state-retired = Retired"),
}

# Built-in search fields by query name. Fields added by data tags are named by their tag.
SEARCH_FIELDS: Mapping[str, Text] = {
    "title": tr(t"vocabulary-search-field-title = Title"),
    "name": tr(t"vocabulary-search-field-name = Name"),
    "subtitle": tr(t"vocabulary-search-field-subtitle = Subtitle"),
    "description": tr(t"vocabulary-search-field-description = Description"),
    "tag": tr(t"vocabulary-search-field-tag = Tag"),
    "restriction": tr(t"vocabulary-search-field-restriction = Restriction"),
    "type": tr(t"vocabulary-search-field-type = Type"),
    "pattern": tr(t"vocabulary-search-field-pattern = Pattern"),
    "creator": tr(t"vocabulary-search-field-creator = Creator"),
    "version": tr(t"vocabulary-search-field-version = Version"),
    "status": tr(t"vocabulary-search-field-status = Status"),
    "kind": tr(t"vocabulary-search-field-kind = Kind"),
    "criterion": tr(t"vocabulary-search-field-criterion = Record criterion"),
    "version_scope": tr(t"vocabulary-search-field-version-scope = Version scope"),
    "record_state": tr(t"vocabulary-search-field-record-state = Record state"),
    "former_holder": tr(t"vocabulary-search-field-former-holder = Former holder"),
    "tied": tr(t"vocabulary-search-field-tied = Tied build"),
    "volume": RECORD_FACTS[RecordFact.FIXED_VOLUME],
    "width": tr(t"vocabulary-search-field-width = Width"),
    "height": tr(t"vocabulary-search-field-height = Height"),
    "depth": tr(t"vocabulary-search-field-depth = Depth"),
    "door_width": tr(t"vocabulary-search-field-door-width = Door width"),
    "door_height": tr(t"vocabulary-search-field-door-height = Door height"),
    "door_depth": tr(t"vocabulary-search-field-door-depth = Door depth"),
    "orientation": tr(t"vocabulary-search-field-orientation = Orientation"),
    "extender_length": tr(t"vocabulary-search-field-extender-length = Extender length"),
    "completion_at": RECORD_FACTS[RecordFact.COMPLETION_AT],
    "created_at": tr(t"vocabulary-search-field-created-at = Submitted"),
    "updated_at": tr(t"vocabulary-search-field-updated-at = Updated"),
    "opening_time": RECORD_FACTS[RecordFact.OPENING],
    "visible_opening_time": RECORD_FACTS[RecordFact.OPENING_VISIBLE],
    "closing_time": RECORD_FACTS[RecordFact.CLOSING],
    "visible_closing_time": RECORD_FACTS[RecordFact.CLOSING_VISIBLE],
    "opening_reset_time": RECORD_FACTS[RecordFact.OPENING_RESET],
    "closing_reset_time": RECORD_FACTS[RecordFact.CLOSING_RESET],
    "extension_time": RECORD_FACTS[RecordFact.EXTENSION],
    "retraction_time": RECORD_FACTS[RecordFact.RETRACTION],
    "extension_reset_time": RECORD_FACTS[RecordFact.EXTENSION_RESET],
    "retraction_reset_time": RECORD_FACTS[RecordFact.RETRACTION_RESET],
}

SEARCH_FIELD_DESCRIPTIONS: Mapping[str, Text] = {
    "title": tr(t"vocabulary-search-field-description-title = Words in the build or record title."),
    "name": tr(t"vocabulary-search-field-description-name = Words in the build's own name."),
    "subtitle": tr(t"vocabulary-search-field-description-subtitle = Words in the line shown under the title."),
    "description": tr(t"vocabulary-search-field-description-description = Words in the build's description."),
    "tag": tr(t"vocabulary-search-field-description-tag = A showcase tag on the build."),
    "restriction": tr(
        t"vocabulary-search-field-description-restriction = A component or design restriction the build follows."
    ),
    "type": tr(t"vocabulary-search-field-description-type = The build's type, such as flush or seamless."),
    "pattern": tr(t"vocabulary-search-field-description-pattern = The door's opening pattern."),
    "creator": tr(t"vocabulary-search-field-description-creator = A credited creator's name or public creator ID."),
    "version": tr(t"vocabulary-search-field-description-version = A Minecraft version the build works in."),
    "status": tr(t"vocabulary-search-field-description-status = The build's review status."),
    "kind": tr(t"vocabulary-search-field-description-kind = What the build is: a door, entrance, extender or utility."),
    "criterion": tr(
        t"vocabulary-search-field-description-criterion = What the record measures, such as fastest or smallest."
    ),
    "version_scope": tr(
        t"vocabulary-search-field-description-version-scope = Whether the record compares builds from every version or only those that work today."
    ),
    "record_state": tr(
        t"vocabulary-search-field-description-record-state = Whether a record is current or retired. Retired records only appear when asked for."
    ),
    "former_holder": tr(
        t"vocabulary-search-field-description-former-holder = The ID of a build that used to hold the record."
    ),
    "tied": tr(t"vocabulary-search-field-description-tied = The ID of a build that shares the record."),
    "volume": tr(t"vocabulary-search-field-description-volume = The number of blocks in the build's bounding box."),
    "width": tr(t"vocabulary-search-field-description-width = The build's width in blocks."),
    "height": tr(t"vocabulary-search-field-description-height = The build's height in blocks."),
    "depth": tr(t"vocabulary-search-field-description-depth = The build's depth in blocks."),
    "door_width": tr(t"vocabulary-search-field-description-door-width = The width of the doorway in blocks."),
    "door_height": tr(t"vocabulary-search-field-description-door-height = The height of the doorway in blocks."),
    "door_depth": tr(t"vocabulary-search-field-description-door-depth = The depth of the doorway in blocks."),
    "orientation": tr(
        t"vocabulary-search-field-description-orientation = How the door is placed, such as door, trapdoor or skydoor."
    ),
    "extender_length": tr(
        t"vocabulary-search-field-description-extender-length = How many blocks the extender reaches."
    ),
    "completion_at": tr(t"vocabulary-search-field-description-completion-at = When the creators finished the build."),
    "created_at": tr(t"vocabulary-search-field-description-created-at = When the build was submitted."),
    "updated_at": tr(t"vocabulary-search-field-description-updated-at = When the build was last changed."),
    "opening_time": tr(t"vocabulary-search-field-description-opening-time = Ticks from input until the door is open."),
    "visible_opening_time": tr(
        t"vocabulary-search-field-description-visible-opening-time = Ticks from input until the door looks open."
    ),
    "closing_time": tr(
        t"vocabulary-search-field-description-closing-time = Ticks from input until the door is closed."
    ),
    "visible_closing_time": tr(
        t"vocabulary-search-field-description-visible-closing-time = Ticks from input until the door looks closed."
    ),
    "opening_reset_time": tr(
        t"vocabulary-search-field-description-opening-reset-time = Ticks after opening until the door accepts input again."
    ),
    "closing_reset_time": tr(
        t"vocabulary-search-field-description-closing-reset-time = Ticks after closing until the door accepts input again."
    ),
    "extension_time": tr(
        t"vocabulary-search-field-description-extension-time = Ticks from input until the extender is extended."
    ),
    "retraction_time": tr(
        t"vocabulary-search-field-description-retraction-time = Ticks from input until the extender is retracted."
    ),
    "extension_reset_time": tr(
        t"vocabulary-search-field-description-extension-reset-time = Ticks after extending until the extender accepts input again."
    ),
    "retraction_reset_time": tr(
        t"vocabulary-search-field-description-retraction-reset-time = Ticks after retracting until the extender accepts input again."
    ),
}

# Contract groups keyed by the values the public API sends; build statuses travel as lowercase names.
VOCABULARY: Mapping[str, Mapping[str, Text]] = {
    "record_criterion": {criterion.value: label for criterion, label in RECORD_CRITERIA.items()},
    "build_kind": {kind.value: label for kind, label in BUILD_KINDS.items()},
    "version_scope": {scope.value: label for scope, label in VERSION_SCOPES.items()},
    "record_fact": {fact.value: label for fact, label in RECORD_FACTS.items()},
    "build_status": {status.name.casefold(): label for status, label in BUILD_STATUSES.items()},
    "search_build_kind": SEARCH_BUILD_KINDS,
    "record_state": RECORD_STATES,
    "search_field": SEARCH_FIELDS,
    "search_field_description": SEARCH_FIELD_DESCRIPTIONS,
}

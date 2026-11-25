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

# Contract groups keyed by the values the public API sends; build statuses travel as lowercase names.
VOCABULARY: Mapping[str, Mapping[str, Text]] = {
    "record_criterion": {criterion.value: label for criterion, label in RECORD_CRITERIA.items()},
    "build_kind": {kind.value: label for kind, label in BUILD_KINDS.items()},
    "version_scope": {scope.value: label for scope, label in VERSION_SCOPES.items()},
    "record_fact": {fact.value: label for fact, label in RECORD_FACTS.items()},
    "build_status": {status.name.casefold(): label for status, label in BUILD_STATUSES.items()},
}

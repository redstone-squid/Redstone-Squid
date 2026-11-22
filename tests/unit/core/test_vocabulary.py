"""Domain value labels must cover every value the website and bot can show."""

import json
from collections.abc import Mapping
from enum import Enum

import pytest

from squid import PROJECT_ROOT
from squid.core.builds.domain.models import BuildStatus
from squid.core.records.domain.models import BuildKind, RecordCriterion, ResolutionStatus, VersionScope
from squid.core.vocabulary import (
    BUILD_KINDS,
    BUILD_STATUSES,
    RECORD_CRITERIA,
    RESOLUTION_STATUSES,
    VERSION_SCOPES,
    VOCABULARY,
)
from squid_i18n import Text


@pytest.mark.parametrize(
    ("labels", "values"),
    [
        (RECORD_CRITERIA, RecordCriterion),
        (BUILD_KINDS, BuildKind),
        (VERSION_SCOPES, VersionScope),
        (RESOLUTION_STATUSES, ResolutionStatus),
        (BUILD_STATUSES, BuildStatus),
    ],
)
def test_every_value_has_a_label(labels: Mapping[Enum, Text], values: type[Enum]) -> None:
    assert set(labels) == set(values)


def test_exported_contract_matches_the_labels() -> None:
    # The website builds against the committed export; scripts/export_vocabulary.py refreshes it.
    contract = json.loads((PROJECT_ROOT / "contracts" / "vocabulary.json").read_text(encoding="utf-8"))
    for locale in contract.values():
        assert {group: set(labels) for group, labels in locale.items()} == {
            group: set(labels) for group, labels in VOCABULARY.items()
        }

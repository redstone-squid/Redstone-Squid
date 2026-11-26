"""Domain value labels must cover every value the website and bot can show."""

import json
from collections.abc import Mapping
from enum import Enum

import pytest

from squid import PROJECT_ROOT
from squid.core.builds.domain.models import BuildStatus
from squid.core.records.domain.models import BuildKind, RecordCriterion, VersionScope
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, RecordState, SearchBuildKind
from squid.core.vocabulary import (
    BUILD_KINDS,
    BUILD_STATUSES,
    RECORD_CRITERIA,
    RECORD_STATES,
    SEARCH_BUILD_KINDS,
    SEARCH_FIELD_DESCRIPTIONS,
    SEARCH_FIELDS,
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
        (BUILD_STATUSES, BuildStatus),
    ],
)
def test_every_value_has_a_label(labels: Mapping[Enum, Text], values: type[Enum]) -> None:
    assert set(labels) == set(values)


@pytest.mark.parametrize(("labels", "values"), [(SEARCH_BUILD_KINDS, SearchBuildKind), (RECORD_STATES, RecordState)])
def test_every_search_value_has_a_label(labels: Mapping[str, Text], values: type[Enum]) -> None:
    assert set(labels) == {member.value for member in values}


def test_every_built_in_search_field_has_a_name_and_description() -> None:
    names = set(DEFAULT_FIELD_REGISTRY.names)

    assert set(SEARCH_FIELDS) == names
    assert set(SEARCH_FIELD_DESCRIPTIONS) == names


def test_enumerated_search_fields_name_a_vocabulary_group_with_every_choice() -> None:
    for field in DEFAULT_FIELD_REGISTRY.definitions:
        choices = field.value_choices()
        if choices is not None:
            assert field.vocabulary is not None, field.name
            assert {choice.value for choice in choices} <= set(VOCABULARY[field.vocabulary]), field.name


def test_exported_contract_matches_the_labels() -> None:
    # The website builds against the committed export; scripts/export_vocabulary.py refreshes it.
    contract = json.loads((PROJECT_ROOT / "contracts" / "vocabulary.json").read_text(encoding="utf-8"))
    for locale in contract.values():
        assert {group: set(labels) for group, labels in locale.items()} == {
            group: set(labels) for group, labels in VOCABULARY.items()
        }

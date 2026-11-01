"""Permission-pattern decoding for stored API keys."""

import pytest
from sqlalchemy.dialects.postgresql import dialect

from squid.auth.models.types import PermissionPatterns
from squid.core.errors import DataIntegrityError
from squid.permissions.domain import Pattern


def test_stored_patterns_are_parsed_once() -> None:
    scopes = PermissionPatterns().process_result_value(["build.**", "account.self.read"], dialect())
    assert scopes == {Pattern.parse("build.**"), Pattern.parse("account.self.read")}


def test_an_unparsable_stored_pattern_is_a_data_integrity_failure() -> None:
    with pytest.raises(DataIntegrityError):
        PermissionPatterns().process_result_value(["build.**", "not a pattern"], dialect())

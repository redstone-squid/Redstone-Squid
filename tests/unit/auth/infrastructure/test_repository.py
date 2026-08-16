"""API-key row mapping."""

import pytest
from whenever import Instant

from squid.auth.infrastructure.models import ApiKey as ApiKeyModel
from squid.auth.infrastructure.repository import _to_domain
from squid.core.errors import DataIntegrityError
from squid.permissions.domain import Pattern

NOW = Instant.from_utc(2026, 8, 8, 12)


def model(*scopes: str) -> ApiKeyModel:
    row = ApiKeyModel(key_id="abc123", secret_hash=b"digest", label="CI", scopes=list(scopes))
    row.id = 1
    row.created_at = NOW
    return row


def test_stored_patterns_are_parsed_once() -> None:
    key = _to_domain(model("build.**", "account.self.read"))

    assert key.scopes == {Pattern.parse("build.**"), Pattern.parse("account.self.read")}


def test_an_unparsable_stored_pattern_is_a_data_integrity_failure() -> None:
    """The column is free text, so a row can only have got here by bypassing the
    service. Failing at the boundary beats matching nothing at request time."""
    with pytest.raises(DataIntegrityError) as raised:
        _to_domain(model("build.**", "not a pattern"))

    assert raised.value.context == {"key_id": "abc123"}

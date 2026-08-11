"""Compatibility namespace and boundary contracts."""

import pytest
from pydantic import ValidationError

from squid.api.capabilities import API_FEATURES, API_VERSION
from squid.api.v1.capabilities import capabilities
from squid.api.v1.schemas.capabilities import ProtocolInterval
from squid.submissions.application import CURRENT_SUBMISSION_PROTOCOL
from squid.submissions.domain import ControlKind


def test_protocol_interval_checks_both_compatibility_boundaries() -> None:
    interval = ProtocolInterval(minimum=2, maximum=4)

    assert interval.supports(1) is False
    assert interval.supports(2) is True
    assert interval.supports(4) is True
    assert interval.supports(5) is False


def test_protocol_interval_rejects_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        ProtocolInterval.model_validate({"minimum": 0, "maximum": 1})
    with pytest.raises(ValidationError):
        ProtocolInterval.model_validate({"minimum": 2, "maximum": 1})


async def test_capabilities_keep_namespaces_independent() -> None:
    response = await capabilities()

    assert response.api.semantic_version == API_VERSION
    assert response.features.identifiers == tuple(sorted(API_FEATURES))
    assert response.protocols.submission.minimum == CURRENT_SUBMISSION_PROTOCOL
    assert response.protocols.submission.maximum == CURRENT_SUBMISSION_PROTOCOL
    assert response.uploads.max_source_bytes > 0
    assert set(response.renderer.controls) == {control.value for control in ControlKind}
    assert response.renderer.capability_identifiers == ("repeatable_text",)
    assert response.sanitization.schematics == "unavailable"

"""API fuzz applicability manifest tests."""

from collections.abc import Callable

import pytest

from tests.fuzz.api.applicability import DEFAULT_APPLICABILITY, ApplicabilityManifest, CheckExemption
from tests.fuzz.api.schemathesis import CHECKS


def test_default_applicability_enables_every_pinned_check() -> None:
    assert DEFAULT_APPLICABILITY.enabled_checks == CHECKS
    assert DEFAULT_APPLICABILITY.applies(operation_id="submission_draft_create", check="use_after_free")


def test_applicability_exemption_disables_one_operation_check_pair() -> None:
    manifest = ApplicabilityManifest(
        enabled_checks=CHECKS,
        exemptions=(
            CheckExemption(
                operation_id="submission_draft_create",
                check="use_after_free",
                reason="Creation responses are producers for use-after-free checks, not deletion sites.",
            ),
        ),
    )

    assert not manifest.applies(operation_id="submission_draft_create", check="use_after_free")
    assert manifest.applies(operation_id="submission_draft_get", check="use_after_free")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CheckExemption("missing_operation", "use_after_free", "documented"),
        lambda: CheckExemption("submission_draft_create", "missing_check", "documented"),
        lambda: CheckExemption("submission_draft_create", "use_after_free", ""),
        lambda: ApplicabilityManifest(("missing_check",)),
        lambda: ApplicabilityManifest(("use_after_free", "use_after_free")),
        lambda: ApplicabilityManifest(
            CHECKS,
            (
                CheckExemption("submission_draft_create", "use_after_free", "first"),
                CheckExemption("submission_draft_create", "use_after_free", "second"),
            ),
        ),
    ],
)
def test_applicability_manifest_refuses_ambiguous_or_stale_entries(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match=r"."):
        factory()

"""Versioned finding metadata, qualification, and redaction tests."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.fuzz.artifacts import (
    FindingCandidateV1,
    QualifiedFindingV1,
    ReproductionV1,
    SensitiveArtifactError,
    assert_artifacts_are_redacted,
    keyed_sensitive_hash,
)

REVISION = "a" * 40
SEED_HASH = "b" * 64
RUN_ONE = "1" * 32
RUN_TWO = "2" * 32
RUN_THREE = "3" * 32


def candidate(*, operations: tuple[str, ...] = ("draft_create",)) -> FindingCandidateV1:
    return FindingCandidateV1(
        engine="schemathesis",
        revision=REVISION,
        profile="anonymous.smoke",
        checker="response_schema_conformance",
        normalized_root_cause="response field status was not a string",
        affected_operations=operations,
        seed_builder_hash=SEED_HASH,
        native_artifact="native-cache/crashes/draft_create.json",
    )


def reproduction(run_id: str, state: str = "product_finding") -> dict[str, str]:
    return {"run_id": run_id, "state": state}


def test_candidate_is_strict_private_and_uses_a_relative_native_reference() -> None:
    finding = candidate()

    assert finding.schema_version == "finding-candidate-v1"
    assert finding.private is True
    assert finding.native_artifact == "native-cache/crashes/draft_create.json"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        FindingCandidateV1.model_validate({**finding.model_dump(), "markdown": "untrusted"})


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/crash.json",
        "../crash.json",
        "native/../../secret",
        "native\\crash.json",
        "native/",
        "native//crash.json",
        ".",
    ],
)
def test_candidate_refuses_unsafe_native_artifact_references(path: str) -> None:
    document = candidate().model_dump()
    document["native_artifact"] = path

    with pytest.raises(ValidationError, match="relative POSIX"):
        FindingCandidateV1.model_validate(document)


def test_primary_fingerprint_treats_operations_as_occurrences() -> None:
    first = candidate(operations=("draft_create",))
    second = candidate(operations=("draft_create", "draft_update"))

    assert first.fingerprint == second.fingerprint


def test_candidate_refuses_duplicate_or_unstable_operation_ids() -> None:
    with pytest.raises(ValidationError, match="unique stable"):
        candidate(operations=("draft_create", "draft_create"))
    with pytest.raises(ValidationError, match="unique stable"):
        candidate(operations=("Draft Create",))


def test_ordinary_qualification_requires_two_distinct_product_reproductions() -> None:
    finding = candidate()
    qualified = QualifiedFindingV1(
        candidate=finding,
        candidate_fingerprint=finding.fingerprint,
        reproductions=(
            ReproductionV1.model_validate(reproduction(RUN_ONE)),
            ReproductionV1.model_validate(reproduction(RUN_TWO)),
        ),
    )

    assert qualified.schema_version == "qualified-finding-v1"
    assert len(qualified.reproductions) == 2


@pytest.mark.parametrize(
    "reproductions",
    [
        (reproduction(RUN_ONE),),
        (reproduction(RUN_ONE), reproduction(RUN_ONE)),
        (reproduction(RUN_ONE), reproduction(RUN_TWO, "pass")),
    ],
)
def test_qualification_refuses_missing_duplicate_or_clean_reproductions(
    reproductions: tuple[dict[str, str], ...],
) -> None:
    finding = candidate()
    with pytest.raises(ValidationError):
        QualifiedFindingV1.model_validate(
            {
                "candidate": finding.model_dump(),
                "candidate_fingerprint": finding.fingerprint,
                "reproductions": reproductions,
            }
        )


def test_performance_qualification_requires_three_healthy_reproductions() -> None:
    finding = candidate()
    qualified = QualifiedFindingV1.model_validate(
        {
            "candidate": finding.model_dump(),
            "candidate_fingerprint": finding.fingerprint,
            "performance": True,
            "reproductions": [reproduction(RUN_ONE), reproduction(RUN_TWO), reproduction(RUN_THREE)],
        }
    )

    assert len(qualified.reproductions) == 3


def test_qualification_refuses_a_mismatched_fingerprint() -> None:
    finding = candidate()
    with pytest.raises(ValidationError, match="fingerprint"):
        QualifiedFindingV1.model_validate(
            {
                "candidate": finding.model_dump(),
                "candidate_fingerprint": "f" * 64,
                "reproductions": [reproduction(RUN_ONE), reproduction(RUN_TWO)],
            }
        )


def test_artifact_scan_rejects_raw_canaries_without_echoing_them(tmp_path: Path) -> None:
    canary = "synthetic-bearer-secret"
    (tmp_path / "events.ndjson").write_text(f'{{"Authorization":"{canary}"}}', encoding="utf-8")

    with pytest.raises(SensitiveArtifactError, match="planted synthetic secret") as caught:
        assert_artifacts_are_redacted(tmp_path, [canary])
    assert canary not in str(caught.value)


def test_artifact_scan_accepts_keyed_canary_hashes(tmp_path: Path) -> None:
    canary = "synthetic-bearer-secret"
    digest = keyed_sensitive_hash(canary, b"k" * 32)
    (tmp_path / "snapshot.json").write_text(digest, encoding="utf-8")

    assert_artifacts_are_redacted(tmp_path, [canary])


def test_artifact_scan_refuses_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("safe", encoding="utf-8")
    (tmp_path / "link").symlink_to(target)

    with pytest.raises(SensitiveArtifactError, match="symbolic links"):
        assert_artifacts_are_redacted(tmp_path, ["canary"])


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("finding-candidate-v1.schema.json", FindingCandidateV1),
        ("qualified-finding-v1.schema.json", QualifiedFindingV1),
    ],
)
def test_committed_finding_schemas_match_models(
    filename: str, model: type[FindingCandidateV1 | QualifiedFindingV1]
) -> None:
    root = Path(__file__).resolve().parents[3]
    committed = json.loads((root / "contracts" / "fuzz" / filename).read_bytes())

    assert committed == model.model_json_schema(mode="validation")

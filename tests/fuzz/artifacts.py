"""Versioned redacted metadata around engine-native fuzzing artifacts."""

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ARTIFACT_FILE_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024
_SAFE_NAME = re.compile(r"[a-z][a-z0-9_.:-]{0,119}")
_OPERATION_ID = re.compile(r"[a-z][a-z0-9_]{0,119}")

type EngineName = Literal["schemathesis", "hypothesis", "atheris", "scenario", "differential"]
type TerminalState = Literal[
    "pass",
    "product_finding",
    "harness_error",
    "infrastructure_error",
    "budget_exhausted",
    "incompatible_replay",
]
type Hex32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
type Hex40 = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
type Hex64 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FuzzArtifactModel(BaseModel):
    """Strict immutable base for data crossing workflow trust boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FindingCandidateV1(FuzzArtifactModel):
    """Redacted metadata pointing to one engine-native reproducer."""

    schema_version: Literal["finding-candidate-v1"] = "finding-candidate-v1"
    engine: EngineName
    revision: Hex40
    profile: str = Field(min_length=1, max_length=120)
    checker: str = Field(min_length=1, max_length=120)
    normalized_root_cause: str = Field(min_length=1, max_length=2_000)
    affected_operations: tuple[str, ...] = Field(default=(), max_length=128)
    seed_builder_hash: Hex64
    native_artifact: str = Field(min_length=1, max_length=240)
    private: bool = True

    @field_validator("profile", "checker")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        if _SAFE_NAME.fullmatch(value) is None:
            msg = "Finding profile and checker names must use the safe identifier vocabulary."
            raise ValueError(msg)
        return value

    @field_validator("normalized_root_cause")
    @classmethod
    def _safe_root_cause(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            msg = "Normalized root causes cannot contain terminal control characters."
            raise ValueError(msg)
        return value

    @field_validator("affected_operations")
    @classmethod
    def _operation_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(_OPERATION_ID.fullmatch(operation) is None for operation in value):
            msg = "Affected operations must be unique stable operation IDs."
            raise ValueError(msg)
        return value

    @field_validator("native_artifact")
    @classmethod
    def _relative_artifact(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != value or "\\" in value:
            msg = "Native artifact references must be relative POSIX file paths."
            raise ValueError(msg)
        return value

    @property
    def fingerprint(self) -> str:
        """Fingerprint the checker and root cause, treating operations as occurrences."""
        document = {
            "checker": self.checker,
            "normalized_root_cause": self.normalized_root_cause,
        }
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()


class ReproductionV1(FuzzArtifactModel):
    """One isolated fresh-stack qualification attempt."""

    run_id: Hex32
    state: TerminalState


class QualifiedFindingV1(FuzzArtifactModel):
    """A candidate reproduced on the exact required number of fresh stacks."""

    schema_version: Literal["qualified-finding-v1"] = "qualified-finding-v1"
    candidate: FindingCandidateV1
    candidate_fingerprint: Hex64
    reproductions: tuple[ReproductionV1, ...]
    performance: bool = False

    @model_validator(mode="after")
    def _exact_qualification(self) -> Self:
        if not hmac.compare_digest(self.candidate_fingerprint, self.candidate.fingerprint):
            msg = "Qualified finding fingerprint does not match its candidate."
            raise ValueError(msg)
        required = 3 if self.performance else 2
        if len(self.reproductions) != required:
            msg = f"Qualified findings require exactly {required} isolated reproductions."
            raise ValueError(msg)
        run_ids = {reproduction.run_id for reproduction in self.reproductions}
        if len(run_ids) != required:
            msg = "Qualified finding reproductions must use distinct fresh-stack run IDs."
            raise ValueError(msg)
        if any(reproduction.state != "product_finding" for reproduction in self.reproductions):
            msg = "Only product-finding reproductions can qualify a candidate."
            raise ValueError(msg)
        return self


class SensitiveArtifactError(RuntimeError):
    """Native artifacts are oversized, unsafe, or contain a planted secret."""


def keyed_sensitive_hash(value: str, key: bytes) -> str:
    """Represent a sensitive comparison value without storing its plaintext."""
    if not value or len(key) < 32:
        msg = "Sensitive hashes require a non-empty value and a 256-bit key."
        raise ValueError(msg)
    return hmac.digest(key, value.encode(), hashlib.sha256).hex()


def assert_artifacts_are_redacted(root: Path, canaries: Iterable[str]) -> None:
    """Refuse symlinks, oversized bundles, and any raw synthetic canary value."""
    encoded_canaries = tuple(canary.encode() for canary in canaries)
    if any(not canary for canary in encoded_canaries):
        msg = "Artifact canaries must be non-empty."
        raise ValueError(msg)
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            msg = "Fuzz artifact bundles cannot contain symbolic links."
            raise SensitiveArtifactError(msg)
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if size > MAX_ARTIFACT_FILE_BYTES or total > MAX_ARTIFACT_TOTAL_BYTES:
            msg = "Fuzz artifact bundle exceeds its audited size limit."
            raise SensitiveArtifactError(msg)
        payload = path.read_bytes()
        if any(canary in payload for canary in encoded_canaries):
            msg = "Fuzz artifact bundle contains a planted synthetic secret."
            raise SensitiveArtifactError(msg)

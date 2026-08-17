"""Applicability manifest for operation/check coverage in API fuzzing."""

import json
from dataclasses import dataclass
from pathlib import Path

from tests.fuzz.api.schemathesis import CHECKS

_OPENAPI_DOCUMENT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _committed_operation_ids() -> frozenset[str]:
    """Read operation ids from the committed document.

    `tests/fuzz/api/schemathesis.py` already asserts the live application matches this file
    byte-for-byte, so reading from it instead of importing `squid.api.openapi.OPERATIONS`
    keeps this manifest valid regardless of whether an operation's contract metadata still
    lives in that table or has moved onto its route.
    """
    document = json.loads(_OPENAPI_DOCUMENT.read_text(encoding="utf-8"))
    return frozenset(
        operation["operationId"]
        for path_item in document["paths"].values()
        for method in _HTTP_METHODS
        if (operation := path_item.get(method)) is not None
    )


_OPERATION_IDS = _committed_operation_ids()
_CHECKS = frozenset(CHECKS)


@dataclass(frozen=True, slots=True)
class CheckExemption:
    """A documented reason that one check is not applicable to one operation."""

    operation_id: str
    check: str
    reason: str

    def __post_init__(self) -> None:
        if self.operation_id not in _OPERATION_IDS:
            msg = f"Unknown operation ID in API fuzz applicability manifest: {self.operation_id}."
            raise ValueError(msg)
        if self.check not in _CHECKS:
            msg = f"Unknown Schemathesis check in API fuzz applicability manifest: {self.check}."
            raise ValueError(msg)
        if not self.reason.strip():
            msg = "API fuzz applicability exemptions require a non-empty reason."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ApplicabilityManifest:
    """The enabled check set plus explicit operation-level exemptions."""

    enabled_checks: tuple[str, ...]
    exemptions: tuple[CheckExemption, ...] = ()

    def __post_init__(self) -> None:
        unknown_checks = set(self.enabled_checks) - _CHECKS
        if unknown_checks:
            names = ", ".join(sorted(unknown_checks))
            msg = f"Unknown enabled Schemathesis checks in API fuzz applicability manifest: {names}."
            raise ValueError(msg)
        if len(self.enabled_checks) != len(set(self.enabled_checks)):
            msg = "API fuzz applicability enabled checks must be unique."
            raise ValueError(msg)
        pairs = [(exemption.operation_id, exemption.check) for exemption in self.exemptions]
        if len(pairs) != len(set(pairs)):
            msg = "API fuzz applicability exemptions must be unique by operation and check."
            raise ValueError(msg)

    def applies(self, *, operation_id: str, check: str) -> bool:
        """Return whether one Schemathesis check is expected to apply to an operation."""
        if operation_id not in _OPERATION_IDS:
            msg = f"Unknown operation ID in API fuzz applicability lookup: {operation_id}."
            raise ValueError(msg)
        if check not in _CHECKS:
            msg = f"Unknown Schemathesis check in API fuzz applicability lookup: {check}."
            raise ValueError(msg)
        if check not in self.enabled_checks:
            return False
        return all(exemption.operation_id != operation_id or exemption.check != check for exemption in self.exemptions)


DEFAULT_APPLICABILITY = ApplicabilityManifest(enabled_checks=CHECKS)

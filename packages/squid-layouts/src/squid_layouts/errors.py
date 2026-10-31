"""Typed failures raised at the logical, planning, and drawing boundaries."""


class LayoutError(Exception):
    """Base class for frontend-neutral layout failures."""


class LayoutInvariantError(LayoutError):
    """The logical document is malformed or has ambiguous identity."""


class LayoutDegradedError(LayoutError):
    """Strict planning rejected a declared degradation."""


class UnsolvableLayoutError(LayoutError):
    """No declared representation satisfies the selected target."""


class DrawInvariantError(LayoutError):
    """A renderer produced output that violates its target contract."""


class ExistingLayoutError(LayoutError):
    """A host-owned view is already invalid before Squid contributes anything to it."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class LimitViolationError(LayoutError):
    """A built view exceeds a Discord limit and the caller forbade clamping."""

    def __init__(self, interventions: list[str]) -> None:
        super().__init__("; ".join(interventions))
        self.interventions = interventions


__all__ = [
    "DrawInvariantError",
    "ExistingLayoutError",
    "LayoutDegradedError",
    "LayoutError",
    "LayoutInvariantError",
    "LimitViolationError",
    "UnsolvableLayoutError",
]

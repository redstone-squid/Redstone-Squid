"""Typed failures raised at the logical, planning, and drawing boundaries."""


class SquidUiError(Exception):
    """Base for UI-specific failures across layout, forms, history, and adapters.

    Some subclasses also inherit standard exception types. Argument validation and
    application callbacks may raise exceptions outside this hierarchy.
    """


class LayoutError(SquidUiError):
    """Base class for frontend-neutral layout failures."""


class LayoutInvariantError(LayoutError):
    """The logical document is malformed or has ambiguous identity."""


class LayoutDegradedError(LayoutError):
    """Strict planning rejected a declared degradation."""


class UnsolvableLayoutError(LayoutError):
    """No declared representation satisfies the selected target.

    Context contains layout metadata only, never rendered text or submitted values.
    """

    def __init__(self, message: str, *, context: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.context: dict[str, object] = dict(context or {})


class LayoutSearchExhaustedError(LayoutError):
    """The bounded search ended without establishing a feasible layout."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.context: dict[str, object] = {"reason": "search_budget_exhausted"}


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
    "LayoutSearchExhaustedError",
    "LimitViolationError",
    "SquidUiError",
    "UnsolvableLayoutError",
]

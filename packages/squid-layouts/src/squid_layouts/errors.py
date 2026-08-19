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

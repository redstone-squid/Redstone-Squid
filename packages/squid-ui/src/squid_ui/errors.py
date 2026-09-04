"""Typed failures raised at the logical, planning, and drawing boundaries."""


class SquidUiError(Exception):
    """Base class for every failure the Squid UI packages raise deliberately.

    Errors outside the layout family keep a standard exception base alongside this one
    (`CodecError` stays a `ValueError`, `HistoryError` a `RuntimeError`), so catching by
    standard type keeps working while `except SquidUiError` covers the whole suite.
    """


class LayoutError(SquidUiError):
    """Base class for frontend-neutral layout failures."""


class LayoutInvariantError(LayoutError):
    """The logical document is malformed or has ambiguous identity.

    Raised by node constructors, `as_document`, the component runtime and every planner for
    author mistakes: a duplicate key, text returned from `render()`, a component embedded
    twice, a control the target cannot host, a `Route.id` over the custom-id budget. Never a
    limits failure.
    """


class LayoutDegradedError(LayoutError):
    """Strict planning rejected a declared degradation.

    Raised by a planner only when the request was made with `strict=True` and the plan carries
    a lossy adaptation such as a dropped component or truncated text.
    """


class UnsolvableLayoutError(LayoutError):
    """No declared representation satisfies the selected target.

    Raised by a planner when every fallback still overspends the target's limits, or when a
    document overflows and cannot paginate (no `Document.key`, or no navigation controls).
    """


class DrawInvariantError(LayoutError):
    """A renderer produced output that violates its target contract.

    Raised by the Discord and Slack renderers when a self-audit of the drawn view fails; a
    planner bug, not an author mistake.
    """


class ExistingLayoutError(LayoutError):
    """A host-owned view is already invalid before Squid contributes anything to it.

    Raised by the Discord fragment and classic adapters when the host's own components fail
    the limits audit, so the failure is not blamed on the fragment being added.
    """

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class LimitViolationError(LayoutError):
    """A built view exceeds a Discord limit and the caller forbade clamping.

    Raised by `squid_ui_discord.conformance.conform` with `strict=True`, by the inspection
    helpers, and by delivery when host and payload files together exceed the attachment cap.
    """

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
    "SquidUiError",
    "UnsolvableLayoutError",
]

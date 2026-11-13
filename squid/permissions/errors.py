"""Public permission explanations without exposing rule subjects."""

from squid.core.errors import AuthorizationError
from squid.core.i18n import tr
from squid.permissions.domain import Decision, Reason


class PermissionDeniedError(AuthorizationError):
    """A known permission node was denied by the resolver."""

    def __init__(self, decision: Decision) -> None:
        if decision.allowed:
            message = "An allowed permission decision cannot produce a denial."
            raise ValueError(message)
        node = decision.node
        if decision.reason is Reason.FORBIDDEN:
            reason = "forbidden"
            message = tr(
                t"permissions-action-forbidden = This action requires {node}, but an explicit forbid rule blocks that permission."
            )
        elif decision.reason is Reason.RULE:
            reason = "explicit_deny"
            message = tr(
                t"permissions-action-denied = This action requires {node}, but an explicit deny rule blocks that permission."
            )
        else:
            reason = "missing_grant"
            message = tr(
                t"permissions-action-missing = This action requires {node}, but your current permissions do not grant it."
            )
        super().__init__(message, public_context={"required_permission": node, "permission_reason": reason})
        self.decision = decision

"""Shared ACL enforcement and public explanations, without rule subjects."""

from squid.core.errors import AuthorizationError, JSONValue
from squid.core.i18n import tr
from squid.permissions.domain import CheckMode, Decision, PermissionCheck, Reason
from squid_i18n import Text


def _explanation(decision: Decision) -> tuple[str, Text]:
    if decision.reason is Reason.FORBIDDEN:
        return "forbidden", tr(t"permissions-forbid-reason = an explicit forbid rule blocks that permission")
    if decision.reason is Reason.RULE:
        return "explicit_deny", tr(t"permissions-deny-reason = an explicit deny rule blocks that permission")
    return "missing_grant", tr(t"permissions-grant-reason = your current permissions do not grant it")


class PermissionDeniedError(AuthorizationError):
    """The denied nodes of an all-or-any permission requirement.

    Construct this from denied decisions only. `require_check` evaluates the
    requirement before selecting those denials, so allowed alternatives never
    appear in the error and one blocked alternative cannot reject an any-check.
    """

    def __init__(self, decision: Decision, *others: Decision, mode: CheckMode = "all") -> None:
        self.decisions = (decision, *others)
        self.mode = mode
        if any(item.allowed for item in self.decisions):
            message = "An allowed permission decision cannot produce a denial."
            raise ValueError(message)
        explanations = tuple(_explanation(item) for item in self.decisions)
        if len(self.decisions) == 1:
            reason, detail = explanations[0]
            node = decision.node
            message = tr(t"permissions-action-requirement = This action requires {node}, but {detail}.")
            public_context: dict[str, JSONValue] = {"required_permission": decision.node, "permission_reason": reason}
        else:
            lead = (
                tr(t"permissions-require-any = This action requires at least one of these permissions:")
                if mode == "any"
                else tr(t"permissions-require-all = This action requires all of these permissions:")
            )
            details = Text.join(
                "\n",
                (
                    Text(t"{item.node} — {detail}")
                    for item, (_, detail) in zip(self.decisions, explanations, strict=True)
                ),
            )
            message = Text(t"{lead}\n{details}")
            public_context = {
                "permission_mode": mode,
                "required_permissions": self.nodes,
                "permission_reasons": {
                    item.node: reason for item, (reason, _) in zip(self.decisions, explanations, strict=True)
                },
            }
        title = (
            tr(t"errors-permission-withheld = Permission withheld")
            if self.forbidden
            else tr(t"errors-missing-permission = Missing permission")
        )
        super().__init__(message, title=title, public_context=public_context)

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(item.node for item in self.decisions)

    @property
    def forbidden(self) -> bool:
        return any(item.reason is Reason.FORBIDDEN for item in self.decisions)


class CredentialPermissionDeniedError(AuthorizationError):
    """The credential's scope excludes a node, independently of its owner's ACL."""

    def __init__(self, node: str) -> None:
        super().__init__(
            tr(
                t"permissions-credential-scope = This action requires {node}, but your credential does not include that permission."
            ),
            public_context={"required_permission": node, "permission_reason": "credential_scope"},
        )


def require_check(result: PermissionCheck, *, error_type: type[PermissionDeniedError] = PermissionDeniedError) -> None:
    """Enforce an evaluated requirement and report only its denied nodes."""
    if not result.decisions:
        message = "A permission requirement must include at least one node."
        raise ValueError(message)
    if not result.allowed:
        denied = tuple(item for item in result.decisions if not item.allowed)
        raise error_type(denied[0], *denied[1:], mode=result.mode)

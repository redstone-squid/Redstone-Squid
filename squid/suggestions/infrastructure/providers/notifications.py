"""Suggestion provider over the caller's own notification subscriptions."""

from collections.abc import Sequence
from typing import Protocol

from squid.notifications import NotificationSubscription
from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest


class AccountSubscriptions(Protocol):
    """Read the subscriptions an account owns."""

    async def subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]: ...


class SubscriptionProvider:
    """Suggest the caller's subscriptions so unfollowing does not require reading an id back.

    Scoped to the viewer by construction: the account id comes from the resolved request rather
    than from anything the caller typed, so one user cannot enumerate another's subscriptions.
    """

    def __init__(self, notifications: AccountSubscriptions) -> None:
        self._notifications = notifications

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        account_id = request.viewer.account_id
        if account_id is None:
            return ()
        return tuple(
            candidate(
                str(subscription.id),
                label=f"#{subscription.id} · {subscription.kind.value}",
                description=_target(subscription),
                kind="subscription",
            )
            for subscription in await self._notifications.subscriptions(account_id)
        )


def _target(subscription: NotificationSubscription) -> str | None:
    if subscription.subject_id is not None:
        return str(subscription.subject_id)
    if subscription.record_filter is None:
        return None
    return ", ".join(f"{name}={value}" for name, value in sorted(subscription.record_filter.as_dict().items()))

"""Authenticated notification management and inbox routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from squid.api.dependencies import CursorSigner, Notifications
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import Page
from squid.api.security import Principal, requires
from squid.api.v1.schemas.notifications import (
    InboxNotificationDetail,
    NotificationPreferencesDetail,
    NotificationPreferenceUpdate,
    NotificationSubscriptionCreate,
    NotificationSubscriptionDetail,
)
from squid.core.errors import AuthenticationError, ValidationError
from squid.permissions.domain.catalogue import ACCOUNT_SELF_READ

router = APIRouter(prefix="/users/me/notifications", tags=["notifications"])
UserPrincipal = Annotated[Principal, Depends(requires(ACCOUNT_SELF_READ))]


@router.get("/preferences", response_model=NotificationPreferencesDetail, responses=responses(401, 403, 503))
async def get_preferences(notifications: Notifications, principal: UserPrincipal) -> NotificationPreferencesDetail:
    """Return disabled defaults before the notification notice has been accepted."""
    return NotificationPreferencesDetail.from_domain(await notifications.preferences(_account_id(principal)))


@router.post(
    "/consent",
    response_model=NotificationPreferencesDetail,
    responses=responses(401, 403, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def accept_notice(
    request: NotificationPreferenceUpdate,
    notifications: Notifications,
    principal: UserPrincipal,
) -> NotificationPreferencesDetail:
    """Accept the notification-specific notice and choose initial channels."""
    preferences = await notifications.accept_notice(
        _account_id(principal),
        web_enabled=request.web_enabled,
        dm_enabled=request.dm_enabled,
    )
    return NotificationPreferencesDetail.from_domain(preferences)


@router.patch(
    "/preferences",
    response_model=NotificationPreferencesDetail,
    responses=responses(401, 403, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def update_preferences(
    request: NotificationPreferenceUpdate,
    notifications: Notifications,
    principal: UserPrincipal,
) -> NotificationPreferencesDetail:
    """Update web and DM channels independently after consent."""
    preferences = await notifications.set_preferences(
        _account_id(principal),
        web_enabled=request.web_enabled,
        dm_enabled=request.dm_enabled,
    )
    return NotificationPreferencesDetail.from_domain(preferences)


@router.get(
    "/subscriptions",
    response_model=list[NotificationSubscriptionDetail],
    responses=responses(401, 403, 503),
)
async def list_subscriptions(
    notifications: Notifications,
    principal: UserPrincipal,
) -> list[NotificationSubscriptionDetail]:
    """List enabled subscriptions owned by the caller."""
    found = await notifications.subscriptions(_account_id(principal))
    return [NotificationSubscriptionDetail.from_domain(item) for item in found]


@router.post(
    "/subscriptions",
    response_model=NotificationSubscriptionDetail,
    status_code=status.HTTP_201_CREATED,
    responses=responses(401, 403, 404, 409, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def create_subscription(
    request: NotificationSubscriptionCreate,
    notifications: Notifications,
    principal: UserPrincipal,
) -> NotificationSubscriptionDetail:
    """Subscribe to a public creator, record competition, or structured filter."""
    subscription = await notifications.subscribe(
        _account_id(principal),
        kind=request.kind,
        subject_id=request.subject_id,
        record_filter=None if request.filter is None else request.filter.to_domain(),
    )
    return NotificationSubscriptionDetail.from_domain(subscription)


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def delete_subscription(
    subscription_id: int,
    notifications: Notifications,
    principal: UserPrincipal,
) -> Response:
    """Remove one caller-owned subscription."""
    await notifications.unsubscribe(_account_id(principal), subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/inbox", response_model=Page[InboxNotificationDetail], responses=responses(400, 401, 403, 503))
async def list_inbox(
    notifications: Notifications,
    signer: CursorSigner,
    principal: UserPrincipal,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> Page[InboxNotificationDetail]:
    """List the caller's web-visible inbox, hiding staff items after access revocation."""
    account_id = _account_id(principal)
    include_staff = bool(principal.discord_id is not None and await notifications.can_view_staff(principal.discord_id))
    binding = f"notifications:account:{account_id}:id-desc"
    after_id = _after_id(signer, cursor, binding)
    found = list(
        await notifications.inbox(
            account_id,
            after_id=after_id,
            limit=page_size + 1,
            include_staff=include_staff,
        )
    )
    has_more = len(found) > page_size
    page = found[:page_size]
    next_cursor = signer.encode({"after_id": page[-1].id}, binding=binding) if has_more and page else None
    return Page(
        items=[InboxNotificationDetail.from_domain(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/inbox/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 503),
    dependencies=[Depends(enforce_request_idempotency)],
)
async def mark_read(
    notification_id: int,
    notifications: Notifications,
    principal: UserPrincipal,
) -> Response:
    """Mark one visible inbox item as read."""
    include_staff = bool(principal.discord_id is not None and await notifications.can_view_staff(principal.discord_id))
    await notifications.mark_read(_account_id(principal), notification_id, include_staff=include_staff)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _account_id(principal: Principal) -> int:
    if principal.kind != "account" or principal.account_id is None:
        raise AuthenticationError
    return principal.account_id


def _after_id(signer: CursorSigner, cursor: str | None, binding: str) -> int | None:
    if cursor is None:
        return None
    value = signer.decode(cursor, binding=binding).get("after_id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = "cursor payload contains an invalid notification identifier"
        raise ValidationError(msg)
    return value

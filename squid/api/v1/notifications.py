"""Authenticated notification management and inbox routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from squid.api.dependencies import Notifications
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    render_page,
    resolve_selector,
)
from squid.api.security import Principal, requires
from squid.api.v1.schemas.notifications import (
    InboxNotificationDetail,
    NotificationPreferencesDetail,
    NotificationPreferenceUpdate,
    NotificationSubscriptionCreate,
    NotificationSubscriptionDetail,
)
from squid.core.errors import AuthenticationError
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
    principal: UserPrincipal,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[InboxNotificationDetail]:
    """List the caller's web-visible inbox, hiding staff items after access revocation."""
    account_id = _account_id(principal)
    include_staff = bool(principal.discord_id is not None and await notifications.can_view_staff(principal.discord_id))
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    page = await notifications.inbox(
        account_id,
        selector=selector,
        page_size=page_size,
        include_staff=include_staff,
    )
    return render_page(page, InboxNotificationDetail.from_domain)


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

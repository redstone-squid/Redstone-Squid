"""Authenticated notification management and inbox routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from squid.api.contract import WEB, WEB_WRITE, browser_only, contract
from squid.api.dependencies import Notifications, Permissions
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
from squid.api.security import Caller, caller_allows, requires
from squid.api.v1.schemas.notifications import (
    InboxNotificationDetail,
    NotificationPreferencesDetail,
    NotificationPreferenceUpdate,
    NotificationSubscriptionCreate,
    NotificationSubscriptionDetail,
)
from squid.core.errors import AuthenticationError
from squid.permissions.domain.catalogue import ACCOUNT_SELF_READ, BUILD_SUBMISSION_VIEW_PENDING

router = APIRouter(prefix="/users/me/notifications", tags=["notifications"])
UserCaller = Annotated[Caller, Depends(requires(ACCOUNT_SELF_READ))]


@router.get(
    "/preferences",
    response_model=NotificationPreferencesDetail,
    responses=responses(401, 403, 503),
    operation_id="notification_preferences_get",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def get_preferences(notifications: Notifications, caller: UserCaller) -> NotificationPreferencesDetail:
    """Return disabled defaults before the notification notice has been accepted."""
    return NotificationPreferencesDetail.from_domain(await notifications.preferences(_account_id(caller)))


@router.patch(
    "/preferences",
    response_model=NotificationPreferencesDetail,
    responses=responses(400, 401, 403, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="notification_preferences_update",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def update_preferences(
    request: NotificationPreferenceUpdate,
    notifications: Notifications,
    caller: UserCaller,
) -> NotificationPreferencesDetail:
    """Update web and DM channels independently after consent."""
    preferences = await notifications.set_preferences(
        _account_id(caller),
        web_enabled=request.web_enabled,
        dm_enabled=request.dm_enabled,
    )
    return NotificationPreferencesDetail.from_domain(preferences)


@router.get(
    "/subscriptions",
    response_model=list[NotificationSubscriptionDetail],
    responses=responses(401, 403, 503),
    operation_id="notification_subscriptions_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_subscriptions(
    notifications: Notifications,
    caller: UserCaller,
) -> list[NotificationSubscriptionDetail]:
    """List enabled subscriptions owned by the caller."""
    found = await notifications.subscriptions(_account_id(caller))
    return [NotificationSubscriptionDetail.from_domain(item) for item in found]


@router.post(
    "/subscriptions",
    response_model=NotificationSubscriptionDetail,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="notification_subscription_create",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def create_subscription(
    request: NotificationSubscriptionCreate,
    notifications: Notifications,
    caller: UserCaller,
) -> NotificationSubscriptionDetail:
    """Subscribe to a public creator, record competition, or structured filter."""
    subscription = await notifications.subscribe(
        _account_id(caller),
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
    operation_id="notification_subscription_delete",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def delete_subscription(
    subscription_id: int,
    notifications: Notifications,
    caller: UserCaller,
) -> Response:
    """Remove one caller-owned subscription."""
    await notifications.unsubscribe(_account_id(caller), subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/inbox",
    response_model=Page[InboxNotificationDetail],
    responses=responses(400, 401, 403, 503),
    operation_id="notification_inbox_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_inbox(
    notifications: Notifications,
    permissions: Permissions,
    caller: UserCaller,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[InboxNotificationDetail]:
    """List the caller's web-visible inbox, hiding staff items after access revocation."""
    account_id = _account_id(caller)
    # Staff notifications are *about* pending submissions, so the node that governs
    # reading those governs reading these. Credential-bounded, so a leaked API key
    # without the node cannot read staff items -- which the old config allowlist,
    # keyed on a snowflake rather than on a credential, could not express.
    include_staff = await caller_allows(permissions, caller, BUILD_SUBMISSION_VIEW_PENDING)
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
    operation_id="notification_inbox_mark_read",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def mark_read(
    notification_id: int,
    notifications: Notifications,
    permissions: Permissions,
    caller: UserCaller,
) -> Response:
    """Mark one visible inbox item as read."""
    # Staff notifications are *about* pending submissions, so the node that governs
    # reading those governs reading these. Credential-bounded, so a leaked API key
    # without the node cannot read staff items -- which the old config allowlist,
    # keyed on a snowflake rather than on a credential, could not express.
    include_staff = await caller_allows(permissions, caller, BUILD_SUBMISSION_VIEW_PENDING)
    await notifications.mark_read(_account_id(caller), notification_id, include_staff=include_staff)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _account_id(caller: Caller) -> int:
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    return caller.account_id

"""Authenticated notification management and inbox routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status

from squid.api.contract import WEB, WEB_WRITE, browser_only, contract
from squid.api.dependencies import Notifications, Permissions, Services
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    resolve_selector,
)
from squid.api.revisions import expected_revision, revision_etag
from squid.api.security import Caller, requires
from squid.api.v1.schemas.notifications import (
    InboxNotificationDetail,
    NotificationPreferencesDetail,
    NotificationPreferenceUpdate,
    NotificationSubscriptionCreate,
    NotificationSubscriptionDetail,
)
from squid.core.errors import AuthenticationError
from squid.edits.domain import EditActor, EditCommand, EditValues, Revision
from squid.notifications import InboxVisibility
from squid.notifications.edits import (
    INBOX_EDIT,
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    SubscriptionsValues,
    inbox_target,
    notification_target,
)
from squid.notifications.errors import NotificationSubscriptionNotFoundError
from squid.permissions.domain.catalogue import ACCOUNT_SELF_READ, BUILD_SUBMISSION_VIEW_PENDING

router = APIRouter(prefix="/users/me/notifications", tags=["notifications"])
UserCaller = Annotated[Caller, Depends(requires(ACCOUNT_SELF_READ))]


async def resolve_inbox_visibility(permissions: Permissions, caller: UserCaller) -> InboxVisibility:
    """Resolve notification visibility once for one authenticated request."""
    return InboxVisibility(
        include_staff=await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING),
    )


type InboxAccess = Annotated[InboxVisibility, Depends(resolve_inbox_visibility)]


@router.get(
    "/preferences",
    response_model=NotificationPreferencesDetail,
    responses=responses(401, 403, 503),
    operation_id="notification_preferences_get",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def get_preferences(
    notifications: Notifications, caller: UserCaller, response: Response
) -> NotificationPreferencesDetail:
    """Return channel preferences, with disabled defaults until privacy consent is accepted."""
    preferences = await notifications.preferences(_account_id(caller))
    response.headers["ETag"] = revision_etag(
        Revision(notification_target(preferences.account_id), preferences.revision)
    )
    return NotificationPreferencesDetail.from_domain(preferences)


@router.patch(
    "/preferences",
    response_model=NotificationPreferencesDetail,
    responses=responses(400, 401, 403, 412, 428, 503),
    dependencies=[Depends(reserve_edit_request)],
    operation_id="notification_preferences_update",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def update_preferences(
    request: NotificationPreferenceUpdate,
    notifications: Notifications,
    caller: UserCaller,
    services: Services,
    response: Response,
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header()] = None,
) -> NotificationPreferencesDetail:
    """Update both channels against the notification revision read by the caller."""
    account_id = _account_id(caller)
    await services.edits.execute(
        EditCommand(
            PREFERENCES_EDIT,
            (expected_revision(notification_target(account_id), if_match),),
            EditValues.of(request.model_dump(mode="json")),
            operation_id,
        ),
        EditActor(caller.permission_subject()),
    )
    return await get_preferences(notifications, caller, response)


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
    response: Response,
) -> list[NotificationSubscriptionDetail]:
    """List enabled subscriptions owned by the caller."""
    snapshot = await notifications.editor_snapshot(_account_id(caller))
    response.headers["ETag"] = revision_etag(
        Revision(notification_target(snapshot.preferences.account_id), snapshot.preferences.revision)
    )
    return [NotificationSubscriptionDetail.from_domain(item) for item in snapshot.subscriptions]


@router.post(
    "/subscriptions",
    response_model=NotificationSubscriptionDetail,
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 404, 409, 412, 422, 428, 503),
    dependencies=[Depends(reserve_edit_request)],
    operation_id="notification_subscription_create",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def create_subscription(
    request: NotificationSubscriptionCreate,
    notifications: Notifications,
    caller: UserCaller,
    services: Services,
    response: Response,
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header()] = None,
) -> NotificationSubscriptionDetail:
    """Follow a creator, record, or filter against the notification settings revision."""
    account_id = _account_id(caller)
    values = request.model_dump(mode="json")
    values["record_filter"] = values.pop("filter")
    result = await services.edits.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (expected_revision(notification_target(account_id), if_match),),
            EditValues.of({"create": values}),
            operation_id,
        ),
        EditActor(caller.permission_subject()),
    )
    if result.receipt is not None:
        subscription = SubscriptionsValues.model_validate(result.receipt.after.unpack()).subscriptions[0]
        response.headers["ETag"] = revision_etag(result.outcome.revisions[0])
        return NotificationSubscriptionDetail(
            id=subscription.id,
            kind=subscription.kind,
            subject_id=subscription.subject_id,
            filter=None if subscription.record_filter is None else dict(subscription.record_filter),
        )
    row, revision = await notifications.subscription_snapshot(
        account_id,
        kind=request.kind,
        subject_id=request.subject_id,
        record_filter=None if request.filter is None else request.filter.to_domain(),
    )
    if row is None:
        raise NotificationSubscriptionNotFoundError
    response.headers["ETag"] = revision_etag(Revision(notification_target(account_id), revision))
    return NotificationSubscriptionDetail.from_domain(row)


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 428, 503),
    dependencies=[Depends(reserve_edit_request)],
    operation_id="notification_subscription_delete",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def delete_subscription(
    subscription_id: int,
    services: Services,
    caller: UserCaller,
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Disable one caller-owned subscription, retaining its identity for history."""
    result = await services.edits.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (expected_revision(notification_target(_account_id(caller)), if_match),),
            EditValues.of({"states": {str(subscription_id): False}}),
            operation_id,
        ),
        EditActor(caller.permission_subject()),
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT, headers={"ETag": revision_etag(result.outcome.revisions[0])}
    )


@router.get(
    "/inbox",
    response_model=Page[InboxNotificationDetail],
    responses=responses(400, 401, 403, 503),
    operation_id="notification_inbox_list",
    openapi_extra=contract(security=[WEB], cli=browser_only()),
)
async def list_inbox(
    notifications: Notifications,
    caller: UserCaller,
    visibility: InboxAccess,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[InboxNotificationDetail]:
    """List the caller's web-visible inbox, hiding staff items after access revocation."""
    account_id = _account_id(caller)
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    page = await notifications.inbox(
        account_id,
        selector=selector,
        page_size=page_size,
        visibility=visibility,
    )
    return Page[InboxNotificationDetail].from_domain(page, InboxNotificationDetail.from_domain)


@router.post(
    "/inbox/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 428, 503),
    dependencies=[Depends(reserve_edit_request)],
    operation_id="notification_inbox_mark_read",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def mark_read(
    notification_id: int,
    services: Services,
    caller: UserCaller,
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Mark one visible inbox item as read, checking its own revision."""
    _account_id(caller)
    result = await services.edits.execute(
        EditCommand(
            INBOX_EDIT,
            (expected_revision(inbox_target(notification_id), if_match),),
            EditValues.of({"states": {str(notification_id): True}}),
            operation_id,
        ),
        EditActor(caller.permission_subject()),
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT, headers={"ETag": revision_etag(result.outcome.revisions[0])}
    )


@router.post(
    "/inbox/{notification_id}/unread",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 428, 503),
    dependencies=[Depends(reserve_edit_request)],
    operation_id="notification_inbox_mark_unread",
    openapi_extra=contract(security=[WEB_WRITE], cli=browser_only()),
)
async def mark_unread(
    notification_id: int,
    services: Services,
    caller: UserCaller,
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Mark one visible inbox item as unread, checking its own revision."""
    _account_id(caller)
    result = await services.edits.execute(
        EditCommand(
            INBOX_EDIT,
            (expected_revision(inbox_target(notification_id), if_match),),
            EditValues.of({"states": {str(notification_id): False}}),
            operation_id,
        ),
        EditActor(caller.permission_subject()),
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT, headers={"ETag": revision_etag(result.outcome.revisions[0])}
    )


def _account_id(caller: Caller) -> int:
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    return caller.account_id

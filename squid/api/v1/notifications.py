"""Authenticated notification management and inbox routes."""

from typing import Annotated
from uuid import UUID

from litestar import Response, Router, delete, get, patch, post
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import HeaderParameter, JSONBody, SkipValidation

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
from squid.edits.domain import EditCommand, EditValues, Revision
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

UserCaller = NamedDependency[SkipValidation[Caller]]


async def resolve_inbox_visibility(permissions: Permissions, caller: UserCaller) -> InboxVisibility:
    """Resolve notification visibility once for one authenticated request."""
    return InboxVisibility(
        include_staff=await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING),
    )


InboxAccess = NamedDependency[SkipValidation[InboxVisibility]]


@get(
    "/preferences",
    responses=responses(401, 403, 503),
    operation_id="notification_preferences_get",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={"caller": Provide(requires(ACCOUNT_SELF_READ))},
)
async def get_preferences(notifications: Notifications, caller: UserCaller) -> Response[NotificationPreferencesDetail]:
    """Return channel preferences, with disabled defaults until privacy consent is accepted."""
    response_headers: dict[str, str] = {}
    preferences = await notifications.preferences(_account_id(caller))
    response_headers["ETag"] = revision_etag(
        Revision(notification_target(preferences.account_id), preferences.revision)
    )
    return Response(
        content=NotificationPreferencesDetail.from_domain(preferences), headers=response_headers, status_code=200
    )


@patch(
    "/preferences",
    responses=responses(400, 401, 403, 412, 422, 503),
    operation_id="notification_preferences_update",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def update_preferences(
    data: JSONBody[NotificationPreferenceUpdate],
    notifications: Notifications,
    caller: UserCaller,
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[NotificationPreferencesDetail]:
    """Update both channels against the notification revision read by the caller."""
    account_id = _account_id(caller)
    await services.edits.execute(
        EditCommand(
            PREFERENCES_EDIT,
            (expected_revision(notification_target(account_id), if_match),),
            EditValues.of(data.model_dump(mode="json")),
            operation_id,
        ),
        caller.to_actor(),
    )
    return await get_preferences.fn(notifications, caller)


@get(
    "/subscriptions",
    responses=responses(401, 403, 503),
    operation_id="notification_subscriptions_list",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={"caller": Provide(requires(ACCOUNT_SELF_READ))},
)
async def list_subscriptions(
    notifications: Notifications, caller: UserCaller
) -> Response[list[NotificationSubscriptionDetail]]:
    """List enabled subscriptions owned by the caller."""
    response_headers: dict[str, str] = {}
    snapshot = await notifications.editor_snapshot(_account_id(caller))
    response_headers["ETag"] = revision_etag(
        Revision(notification_target(snapshot.preferences.account_id), snapshot.preferences.revision)
    )
    return Response(
        content=[NotificationSubscriptionDetail.from_domain(item) for item in snapshot.subscriptions],
        headers=response_headers,
        status_code=200,
    )


@post(
    "/subscriptions",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 404, 409, 412, 422, 503),
    operation_id="notification_subscription_create",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def create_subscription(
    data: JSONBody[NotificationSubscriptionCreate],
    notifications: Notifications,
    caller: UserCaller,
    services: Services,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[NotificationSubscriptionDetail]:
    """Follow a creator, record, or filter against the notification settings revision."""
    response_headers: dict[str, str] = {}
    account_id = _account_id(caller)
    values = data.model_dump(mode="json")
    values["record_filter"] = values.pop("filter")
    result = await services.edits.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (expected_revision(notification_target(account_id), if_match),),
            EditValues.of({"create": values}),
            operation_id,
        ),
        caller.to_actor(),
    )
    if result.receipt is not None:
        subscription = SubscriptionsValues.model_validate(result.receipt.after.unpack()).subscriptions[0]
        response_headers["ETag"] = revision_etag(result.outcome.revisions[0])
        return Response(
            content=NotificationSubscriptionDetail(
                id=subscription.id,
                kind=subscription.kind,
                subject_id=subscription.subject_id,
                filter=None if subscription.record_filter is None else dict(subscription.record_filter),
            ),
            headers=response_headers,
            status_code=status.HTTP_201_CREATED,
        )
    row, revision = await notifications.subscription_snapshot(
        account_id,
        kind=data.kind,
        subject_id=data.subject_id,
        record_filter=None if data.filter is None else data.filter.to_domain(),
    )
    if row is None:
        raise NotificationSubscriptionNotFoundError
    response_headers["ETag"] = revision_etag(Revision(notification_target(account_id), revision))
    return Response(
        content=NotificationSubscriptionDetail.from_domain(row),
        headers=response_headers,
        status_code=status.HTTP_201_CREATED,
    )


@delete(
    "/subscriptions/{subscription_id:int}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 503),
    operation_id="notification_subscription_delete",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def delete_subscription(
    subscription_id: int,
    services: Services,
    caller: UserCaller,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Disable one caller-owned subscription, retaining its identity for history."""
    result = await services.edits.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (expected_revision(notification_target(_account_id(caller)), if_match),),
            EditValues.of({"states": {str(subscription_id): False}}),
            operation_id,
        ),
        caller.to_actor(),
    )
    return Response(
        content=None,
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"ETag": revision_etag(result.outcome.revisions[0])},
    )


@get(
    "/inbox",
    responses=responses(400, 401, 403, 422, 503),
    operation_id="notification_inbox_list",
    opt=contract(security=[WEB], cli=browser_only()),
    status_code=200,
    dependencies={
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
        "visibility": Provide(resolve_inbox_visibility),
    },
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
    page = await notifications.inbox(account_id, selector=selector, page_size=page_size, visibility=visibility)
    return Page[InboxNotificationDetail].from_domain(page, InboxNotificationDetail.from_domain)


@post(
    "/inbox/{notification_id:int}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 503),
    operation_id="notification_inbox_mark_read",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def mark_read(
    notification_id: int,
    services: Services,
    caller: UserCaller,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Mark one visible inbox item as read, checking its own revision."""
    _account_id(caller)
    result = await services.edits.execute(
        EditCommand(
            INBOX_EDIT,
            (expected_revision(inbox_target(notification_id), if_match),),
            EditValues.of({"states": {str(notification_id): True}}),
            operation_id,
        ),
        caller.to_actor(),
    )
    return Response(
        content=None,
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"ETag": revision_etag(result.outcome.revisions[0])},
    )


@post(
    "/inbox/{notification_id:int}/unread",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 503),
    operation_id="notification_inbox_mark_unread",
    opt=contract(security=[WEB_WRITE], cli=browser_only()),
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "caller": Provide(requires(ACCOUNT_SELF_READ)),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def mark_unread(
    notification_id: int,
    services: Services,
    caller: UserCaller,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Mark one visible inbox item as unread, checking its own revision."""
    _account_id(caller)
    result = await services.edits.execute(
        EditCommand(
            INBOX_EDIT,
            (expected_revision(inbox_target(notification_id), if_match),),
            EditValues.of({"states": {str(notification_id): False}}),
            operation_id,
        ),
        caller.to_actor(),
    )
    return Response(
        content=None,
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"ETag": revision_etag(result.outcome.revisions[0])},
    )


def _account_id(caller: Caller) -> int:
    if caller.kind != "account" or caller.account_id is None:
        raise AuthenticationError
    return caller.account_id


router = Router(
    path="/users/me/notifications",
    tags=["notifications"],
    route_handlers=[
        get_preferences,
        update_preferences,
        list_subscriptions,
        create_subscription,
        delete_subscription,
        list_inbox,
        mark_read,
        mark_unread,
    ],
)

"""Authenticated notification management and inbox routes."""

from typing import Annotated
from uuid import UUID

from litestar import Response, Router, delete, get, patch, post
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, HeaderParameter, JSONBody, QueryParameter, SkipValidation

from squid.api.contract import WEB, WEB_WRITE, browser_only, contract
from squid.api.dependencies import Notifications, Services, get_notifications
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    PageSizeParam,
    resolve_selector,
)
from squid.api.revisions import expected_revision, revision_etag
from squid.api.security import Caller, requires
from squid.api.v1.schemas.notifications import (
    InboxPage,
    NotificationPreferencesDetail,
    NotificationPreferenceUpdate,
    NotificationSubscriptionCreate,
    NotificationSubscriptionDetail,
)
from squid.core.edits.domain import EditCommand, EditValues, LatestRevision, Revision
from squid.core.errors import AuthenticationError
from squid.core.notifications import SubscriptionKind
from squid.core.notifications.edits import (
    INBOX_EDIT,
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    inbox_target,
    notification_target,
)
from squid.core.notifications.errors import NotificationSubscriptionNotFoundError
from squid.core.permissions.domain.catalogue import ACCOUNT_SELF_READ

UserCaller = NamedDependency[SkipValidation[Caller]]


@get(
    "/preferences",
    responses=responses(401, 403, 503),
    operation_id="notification_preferences_get",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
)
async def get_preferences(notifications: Notifications, caller: UserCaller) -> Response[NotificationPreferencesDetail]:
    """Return channel preferences, with disabled defaults until privacy consent is accepted."""
    response_headers: dict[str, str] = {}
    _account_id(caller)
    preferences = await notifications.preferences(context=caller.context())
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
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
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
        context=caller.context(),
    )
    return await get_preferences.fn(notifications, caller)


@get(
    "/subscriptions",
    responses=responses(401, 403, 503),
    operation_id="notification_subscriptions_list",
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
)
async def list_subscriptions(
    notifications: Notifications,
    caller: UserCaller,
    kind: Annotated[SubscriptionKind | None, QueryParameter(description="Only list follows of this kind.")] = None,
    subject_id: Annotated[
        UUID | None, QueryParameter(description="Only list follows of this creator or record.")
    ] = None,
) -> Response[list[NotificationSubscriptionDetail]]:
    """List enabled subscriptions owned by the caller.

    Filter by kind and subject to learn whether the caller follows one creator or record.
    """
    _account_id(caller)
    snapshot = await notifications.editor_snapshot(context=caller.context())
    return Response(
        content=[
            NotificationSubscriptionDetail.from_domain(follow)
            for follow in snapshot.follows
            if (kind is None or follow.subscription.kind is kind)
            and (subject_id is None or follow.subscription.subject_id == subject_id)
        ],
        headers={
            "ETag": revision_etag(
                Revision(notification_target(snapshot.preferences.account_id), snapshot.preferences.revision)
            )
        },
        status_code=200,
    )


@post(
    "/subscriptions",
    status_code=status.HTTP_201_CREATED,
    responses=responses(400, 401, 403, 404, 409, 412, 422, 503),
    operation_id="notification_subscription_create",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    guards=[requires(ACCOUNT_SELF_READ)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
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
    account_id = _account_id(caller)
    values = data.model_dump(mode="json")
    values["record_filter"] = values.pop("filter")
    await services.edits.execute(
        EditCommand(
            SUBSCRIPTIONS_EDIT,
            (expected_revision(notification_target(account_id), if_match),),
            EditValues.of({"create": values}),
            operation_id,
        ),
        context=caller.context(),
    )
    # A replayed request also lands here, so read the follow back rather than trusting the receipt.
    follow, revision = await notifications.subscription_snapshot(
        context=caller.context(),
        kind=data.kind,
        subject_id=data.subject_id,
        record_filter=None if data.filter is None else data.filter.to_domain(),
    )
    if follow is None:
        raise NotificationSubscriptionNotFoundError
    return Response(
        content=NotificationSubscriptionDetail.from_domain(follow),
        headers={"ETag": revision_etag(Revision(notification_target(account_id), revision))},
        status_code=status.HTTP_201_CREATED,
    )


@delete(
    "/subscriptions/{subscription_id:int}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 503),
    operation_id="notification_subscription_delete",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    guards=[requires(ACCOUNT_SELF_READ)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def delete_subscription(
    subscription_id: FromPath[int],
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
        context=caller.context(),
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
    security=[WEB],
    opt=contract(cli=browser_only()),
    status_code=200,
    guards=[requires(ACCOUNT_SELF_READ)],
)
async def list_inbox(
    notifications: Notifications,
    caller: UserCaller,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
    unread_only: Annotated[bool, QueryParameter(description="Only list unread items.")] = False,
) -> InboxPage:
    """List the caller's web-visible inbox, hiding staff items after access revocation."""
    _account_id(caller)
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    inbox = await notifications.inbox(
        context=caller.context(), selector=selector, page_size=page_size, unread_only=unread_only
    )
    return InboxPage.from_domain(inbox)


@post(
    "/inbox/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 409, 503),
    operation_id="notification_inbox_mark_all_read",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    guards=[requires(ACCOUNT_SELF_READ)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def mark_all_read(
    notifications: Notifications,
    services: Services,
    caller: UserCaller,
    operation_id: NamedDependency[SkipValidation[UUID]],
    through_id: Annotated[
        int | None,
        QueryParameter(ge=1, description="Leave newer items unread, such as those that arrived after the page loaded."),
    ] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Mark every visible unread inbox item read, as one undoable edit."""
    _account_id(caller)
    unread = await notifications.unread_ids(context=caller.context(), through_id=through_id)
    if unread:
        await services.edits.execute(
            EditCommand(
                INBOX_EDIT,
                tuple(LatestRevision(inbox_target(notification_id)) for notification_id in unread),
                EditValues.of({"states": {str(notification_id): True for notification_id in unread}}),
                operation_id,
            ),
            context=caller.context(),
        )
    return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)


@post(
    "/inbox/{notification_id:int}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(401, 403, 404, 409, 412, 503),
    operation_id="notification_inbox_mark_read",
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    guards=[requires(ACCOUNT_SELF_READ)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def mark_read(
    notification_id: FromPath[int],
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
        context=caller.context(),
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
    security=[WEB_WRITE],
    opt=contract(cli=browser_only()),
    guards=[requires(ACCOUNT_SELF_READ)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def mark_unread(
    notification_id: FromPath[int],
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
        context=caller.context(),
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
    dependencies={
        "notifications": Provide(get_notifications, sync_to_thread=False),
    },
    path="/users/me/notifications",
    tags=["notifications"],
    route_handlers=[
        get_preferences,
        update_preferences,
        list_subscriptions,
        create_subscription,
        delete_subscription,
        list_inbox,
        mark_all_read,
        mark_read,
        mark_unread,
    ],
)

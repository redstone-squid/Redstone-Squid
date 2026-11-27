"""Global runtime settings, authorized identically for every HTTP credential surface."""

from typing import Annotated
from uuid import UUID

from litestar import Response, Router, get, patch
from litestar.di import NamedDependency, Provide
from litestar.params import HeaderParameter, JSONBody, SkipValidation

from squid.api.contract import DEVICE, MINECRAFT, PAPER, SERVICE, WEB, WEB_WRITE, cli_command, contract, transport_only
from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.revisions import expected_revision, revision_etag
from squid.api.security import Caller, requires
from squid.api.v1.schemas.settings import (
    DiscordChannelDetail,
    DiscordRoleDetail,
    RuntimeSettingsDetail,
    RuntimeSettingsPatch,
)
from squid.core.edits.domain import EditCommand, EditValues, Revision
from squid.core.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW
from squid.core.settings.application.edits import RUNTIME_EDIT, RUNTIME_TARGET
from squid.core.settings.domain.runtime import RuntimeSettings

ViewCaller = NamedDependency[SkipValidation[Caller]]
EditCaller = NamedDependency[SkipValidation[Caller]]


@get(
    "/",
    responses=responses(401, 403, 503),
    operation_id="runtime_settings_get",
    security=[WEB, DEVICE, MINECRAFT, MINECRAFT | PAPER, SERVICE],
    opt=contract(
        cli=cli_command("settings.show", interaction="direct"),
        scopes=("settings.runtime.view",),
    ),
    status_code=200,
    guards=[requires(SETTINGS_RUNTIME_VIEW)],
)
async def get_runtime_settings(services: Services, caller: ViewCaller) -> Response[RuntimeSettingsDetail]:
    """Read current global settings, naming each Discord ID.

    Discord IDs are decimal strings. A name is null when Discord no longer knows
    the ID; when Discord is unavailable, `names_resolved` is false.
    """
    response_headers: dict[str, str] = {}
    settings, revision = await services.settings.runtime_snapshot(context=caller.context())
    names = await services.settings.runtime_names(settings, context=caller.context())
    response_headers["ETag"] = revision_etag(Revision(RUNTIME_TARGET, revision))
    return Response(
        content=RuntimeSettingsDetail.from_domain(settings, revision=revision, names=names),
        headers=response_headers,
        status_code=200,
    )


@patch(
    "/",
    responses=responses(401, 403, 412, 422, 503),
    operation_id="runtime_settings_update",
    security=[WEB_WRITE, DEVICE, MINECRAFT, MINECRAFT | PAPER, SERVICE],
    opt=contract(
        cli=cli_command("settings.set", interaction="direct"),
        scopes=("settings.runtime.edit",),
    ),
    status_code=200,
    guards=[requires(SETTINGS_RUNTIME_EDIT)],
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def update_runtime_settings(
    data: JSONBody[RuntimeSettingsPatch],
    services: Services,
    caller: EditCaller,
    operation_id: NamedDependency[SkipValidation[UUID]],
    if_match: Annotated[str | None, HeaderParameter(name="If-Match")] = None,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[RuntimeSettingsDetail]:
    """Patch current settings, or require a specific revision with If-Match.

    New vote defaults affect new sessions only. A Discord ID of the wrong kind, such
    as a role in a channel field, fails with a `discord_channel`, `discord_role` or
    `discord_user` rule issue whose reason is `unknown` or `category`. While Discord
    is unavailable, IDs are stored unchecked.
    """
    response_headers: dict[str, str] = {}
    caller.require_consented_account()
    version = expected_revision(RUNTIME_TARGET, if_match)
    result = await services.edits.execute(
        EditCommand(RUNTIME_EDIT, (version,), EditValues.of(data.model_dump(exclude_unset=True)), operation_id),
        context=caller.context(),
    )
    if result.receipt is None:
        settings, revision = await services.settings.runtime_snapshot(context=caller.context(), for_edit=True)
    else:
        settings = RuntimeSettings.model_validate(result.receipt.after.unpack())
        revision = result.outcome.revisions[0].value
    names = await services.settings.runtime_names(settings, context=caller.context())
    response_headers["ETag"] = revision_etag(Revision(RUNTIME_TARGET, revision))
    return Response(
        content=RuntimeSettingsDetail.from_domain(settings, revision=revision, names=names),
        headers=response_headers,
        status_code=200,
    )


@get(
    "/channels",
    responses=responses(401, 403, 503),
    operation_id="runtime_settings_channels",
    security=[WEB, DEVICE, SERVICE],
    opt=contract(cli=transport_only(), scopes=("settings.runtime.view",)),
    status_code=200,
    guards=[requires(SETTINGS_RUNTIME_VIEW)],
)
async def list_setting_channels(services: Services, caller: ViewCaller) -> list[DiscordChannelDetail]:
    """List the owner guild's channels, for picking a channel setting."""
    return DiscordChannelDetail.listing(await services.settings.owner_guild_channels(context=caller.context()))


@get(
    "/roles",
    responses=responses(401, 403, 503),
    operation_id="runtime_settings_roles",
    security=[WEB, DEVICE, SERVICE],
    opt=contract(cli=transport_only(), scopes=("settings.runtime.view",)),
    status_code=200,
    guards=[requires(SETTINGS_RUNTIME_VIEW)],
)
async def list_setting_roles(services: Services, caller: ViewCaller) -> list[DiscordRoleDetail]:
    """List the owner guild's roles, highest first, for picking a role setting."""
    return DiscordRoleDetail.listing(await services.settings.owner_guild_roles(context=caller.context()))


router = Router(
    opt={"x-squid-private-response": True},
    path="/settings/runtime",
    tags=["settings"],
    route_handlers=[get_runtime_settings, update_runtime_settings, list_setting_channels, list_setting_roles],
)

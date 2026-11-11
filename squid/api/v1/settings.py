"""Global runtime settings, authorized identically for every HTTP credential surface."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from squid.api.contract import DEVICE, MINECRAFT, PAPER, SERVICE, WEB, WEB_WRITE, cli_command, contract
from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.revisions import expected_revision, revision_etag
from squid.api.security import Caller, requires
from squid.api.v1.schemas.settings import RuntimeSettingsDetail, RuntimeSettingsPatch
from squid.edits.domain import EditActor, EditCommand, EditValues, Revision
from squid.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW
from squid.settings.application.edits import RUNTIME_EDIT, RUNTIME_TARGET
from squid.settings.domain.runtime import RuntimeSettings

router = APIRouter(prefix="/settings/runtime", tags=["settings"])
ViewCaller = Annotated[Caller, Depends(requires(SETTINGS_RUNTIME_VIEW))]
EditCaller = Annotated[Caller, Depends(requires(SETTINGS_RUNTIME_EDIT))]


@router.get(
    "",
    response_model=RuntimeSettingsDetail,
    responses=responses(401, 403, 503),
    operation_id="runtime_settings_get",
    openapi_extra=contract(
        security=[WEB, DEVICE, MINECRAFT, MINECRAFT | PAPER, SERVICE],
        cli=cli_command("settings.show", interaction="direct"),
        scopes=("settings.runtime.view",),
    ),
)
async def get_runtime_settings(services: Services, caller: ViewCaller, response: Response) -> RuntimeSettingsDetail:
    """Read current global settings. Discord IDs are returned as decimal strings."""
    settings, revision = await services.settings.runtime_snapshot()
    response.headers["ETag"] = revision_etag(Revision(RUNTIME_TARGET, revision))
    return RuntimeSettingsDetail.from_domain(settings, revision=revision)


@router.patch(
    "",
    response_model=RuntimeSettingsDetail,
    responses=responses(401, 403, 412, 422, 428, 503),
    dependencies=[Depends(reserve_edit_request)],
    operation_id="runtime_settings_update",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT, MINECRAFT | PAPER, SERVICE],
        cli=cli_command("settings.set", interaction="direct"),
        scopes=("settings.runtime.edit",),
    ),
)
async def update_runtime_settings(
    body: RuntimeSettingsPatch,
    services: Services,
    caller: EditCaller,
    response: Response,
    operation_id: Annotated[UUID, Depends(edit_operation_id)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RuntimeSettingsDetail:
    """Patch settings and audit the internal account. New vote defaults affect new sessions only."""
    caller.require_consented_account()
    version = expected_revision(RUNTIME_TARGET, if_match)
    result = await services.edits.execute(
        EditCommand(RUNTIME_EDIT, (version,), EditValues.of(body.model_dump(exclude_unset=True)), operation_id),
        EditActor(caller.permission_subject()),
    )
    if result.receipt is None:
        settings, revision = await services.settings.runtime_snapshot()
    else:
        settings = RuntimeSettings.model_validate(result.receipt.after.unpack())
        revision = result.outcome.revisions[0].value
    response.headers["ETag"] = revision_etag(Revision(RUNTIME_TARGET, revision))
    return RuntimeSettingsDetail.from_domain(settings, revision=revision)

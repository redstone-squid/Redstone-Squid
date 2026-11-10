"""Global runtime settings, authorized identically for every HTTP credential surface."""

from typing import Annotated

from fastapi import APIRouter, Depends

from squid.api.contract import DEVICE, MINECRAFT, PAPER, SERVICE, WEB, WEB_WRITE, cli_command, contract
from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_request
from squid.api.security import Caller, requires
from squid.api.v1.schemas.settings import RuntimeSettingsDetail, RuntimeSettingsPatch
from squid.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW

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
async def get_runtime_settings(services: Services, caller: ViewCaller) -> RuntimeSettingsDetail:
    """Read current global settings. Discord IDs are returned as decimal strings."""
    return RuntimeSettingsDetail.from_domain(await services.settings.get_runtime())


@router.patch(
    "",
    response_model=RuntimeSettingsDetail,
    responses=responses(401, 403, 422, 503),
    dependencies=[Depends(reserve_idempotent_request)],
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
) -> RuntimeSettingsDetail:
    """Patch settings and audit the internal account. New vote defaults affect new sessions only."""
    account_id = caller.require_consented_account()
    updated = await services.settings.update_runtime(body.model_dump(exclude_unset=True), actor_account_id=account_id)
    return RuntimeSettingsDetail.from_domain(updated)

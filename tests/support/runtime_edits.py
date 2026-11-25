"""Drive runtime-setting fixture writes through the production executor."""

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditExecutor
from squid.core.edits.domain import EditCommand, EditValues, LatestRevision
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.settings.application import SettingsService
from squid.core.settings.application.edits import RUNTIME_EDIT, RUNTIME_TARGET, RuntimeSettingsEdit
from squid.core.unit_of_work import UnitOfWorkFactory


async def edit_runtime(service: SettingsService, changes: Mapping[str, object], *, actor_account_id: int) -> None:
    factory = cast(UnitOfWorkFactory[SqlAlchemyUnitOfWork], service._uow_factory)
    executor = EditExecutor(
        factory,
        {
            RUNTIME_EDIT: RuntimeSettingsEdit(PermissionService(uow_factory=factory), service._runtime_defaults),
        },
    )
    await executor.execute(
        EditCommand(
            RUNTIME_EDIT, (LatestRevision(RUNTIME_TARGET),), EditValues.of(cast(Mapping[str, JsonValue], changes))
        ),
        context=ActingContext.direct(Actor(account_id=actor_account_id, is_bot_owner=True)),
    )

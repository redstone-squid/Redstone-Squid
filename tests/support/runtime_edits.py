"""Drive runtime-setting fixture writes through the production executor."""

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from squid.core.actors import Actor
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, EditValues, LatestRevision
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.settings.application import SettingsService
from squid.settings.application.edits import RUNTIME_EDIT, RUNTIME_TARGET, RuntimeSettingsEdit
from squid.unit_of_work import UnitOfWorkFactory


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
        Actor(account_id=actor_account_id, is_bot_owner=True),
    )

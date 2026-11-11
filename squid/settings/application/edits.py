"""Declarative runtime setting edits with current authority and aggregate revisions."""

from typing import Protocol

from squid.core.errors import AuthorizationError
from squid.edits.application import EditUnitOfWork, FieldEditHandler
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT
from squid.persistence.revisions import require_revision
from squid.settings.application.ports import SettingsStore
from squid.settings.domain.runtime import RuntimeSettings
from squid.settings.models import RuntimeSetting

RUNTIME_TARGET = EditTarget("runtime-settings", "1")
RUNTIME_EDIT = "settings.runtime"


class SettingsEditStore(SettingsStore, Protocol):
    async def flush_runtime_revision(self, row: RuntimeSetting, expected: Revision) -> Revision: ...


class SettingsEditUnitOfWork(EditUnitOfWork, PermissionUnitOfWork, Protocol):
    @property
    def settings(self) -> SettingsEditStore: ...


class RuntimeSettingsEdit(FieldEditHandler[SettingsEditUnitOfWork]):
    fields = frozenset(RuntimeSettings.model_fields)

    def __init__(self, permissions: PermissionService, defaults: RuntimeSettings) -> None:
        self._permissions = permissions
        self._defaults = defaults

    async def load(self, uow: SettingsEditUnitOfWork, command: EditCommand) -> EditValues:
        if len(command.expected) != 1 or command.expected[0].target != RUNTIME_TARGET:
            message = "A runtime settings edit must guard the singleton revision"
            raise ValueError(message)
        row = await uow.settings.runtime(self._defaults, for_update=True)
        require_revision(command.expected[0], row.revision)
        return EditValues.of(RuntimeSettings.model_validate(row).model_dump(mode="json"))

    async def validate(
        self,
        uow: SettingsEditUnitOfWork,
        actor: Subject,
        command: EditCommand,
        values: EditValues,
    ) -> EditValues:
        if actor.account_id is None or not await self._permissions.allows_in(uow, actor, SETTINGS_RUNTIME_EDIT):
            raise AuthorizationError
        return EditValues.of(RuntimeSettings.model_validate(values.unpack()).model_dump(mode="json"))

    async def apply(
        self,
        uow: SettingsEditUnitOfWork,
        actor: Subject,
        command: EditCommand,
        prepared: PreparedEdit,
    ) -> tuple[Revision, ...]:
        row = await uow.settings.runtime(self._defaults, for_update=True)
        before = RuntimeSettings.model_validate(prepared.before.unpack())
        after = RuntimeSettings.model_validate(prepared.after.unpack())
        for name, value in after.model_dump().items():
            setattr(row, name, value)
        assert actor.account_id is not None
        await uow.settings.record_runtime_change(actor.account_id, before, after)
        return (await uow.settings.flush_runtime_revision(row, command.expected[0]),)

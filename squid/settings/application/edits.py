"""Declarative runtime setting edits with current authority and aggregate revisions."""

from dataclasses import dataclass
from typing import Protocol

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditTarget, Revision
from squid.edits.fields import Aggregate, FieldCommand
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT
from squid.settings.application.ports import GuildSettingsUnitOfWork, SettingsStore
from squid.settings.domain.guild import CHANNEL_FIELDS, GuildSettings
from squid.settings.domain.runtime import RuntimeSettings
from squid.settings.models import RuntimeSetting, ServerSetting
from squid.voting.domain.models import VoteConfiguration, VoteKind

RUNTIME_TARGET = EditTarget("runtime-settings", "1")
RUNTIME_EDIT = "settings.runtime"


class SettingsEditStore(SettingsStore, Protocol):
    async def flush_guild_revision(self, row: ServerSetting, expected: Revision) -> Revision: ...

    async def flush_runtime_revision(self, row: RuntimeSetting, expected: Revision) -> Revision: ...


class SettingsEditUnitOfWork(EditUnitOfWork, PermissionUnitOfWork, GuildSettingsUnitOfWork, Protocol):
    @property
    def settings(self) -> SettingsEditStore: ...


class RuntimeSettingsEdit(FieldCommand[SettingsEditUnitOfWork, RuntimeSetting, RuntimeSettings]):
    def __init__(self, permissions: PermissionService, defaults: RuntimeSettings) -> None:
        async def load(uow: SettingsEditUnitOfWork, command: EditCommand) -> RuntimeSetting:
            return await uow.settings.runtime(defaults, for_update=True)

        async def flush(uow: SettingsEditUnitOfWork, row: RuntimeSetting, expected: Revision) -> Revision:
            return await uow.settings.flush_runtime_revision(row, expected)

        async def authorize(uow: SettingsEditUnitOfWork, actor: Actor, row: RuntimeSetting) -> None:
            if actor.account_id is None or not await permissions.allows_in(uow, actor, SETTINGS_RUNTIME_EDIT):
                raise AuthorizationError

        async def audit(
            uow: SettingsEditUnitOfWork, actor: Actor, before: RuntimeSettings, after: RuntimeSettings
        ) -> None:
            await uow.settings.record_runtime_change(actor.require_account_id(), before, after)

        super().__init__(
            Aggregate(RUNTIME_TARGET, load, lambda row: row.revision, flush),
            RuntimeSettings,
            authorize,
            audit=audit,
        )


GUILD_EDIT = "settings.guild"


def guild_target(guild_id: int) -> EditTarget:
    return EditTarget("guild-settings", str(guild_id))


async def guild_snapshot(uow: GuildSettingsUnitOfWork, guild_id: int) -> tuple[GuildSettings, int]:
    """Read channels and child vote settings under their shared root lock."""
    row = await uow.settings.get_or_create_for_update(guild_id)
    return await _guild_values(uow, row), row.revision


async def _guild_values(uow: GuildSettingsUnitOfWork, row: ServerSetting) -> GuildSettings:
    guild_id = row.server_id
    voting = {}
    for kind in VoteKind:
        voting[kind] = VoteConfiguration(
            await uow.votes.get_emoji_preset(guild_id, kind),
            tuple(sorted(await uow.votes.get_role_weights(guild_id, kind), key=lambda weight: weight.role_id)),
        )
    return GuildSettings(
        **{name: getattr(row, name) for name in CHANNEL_FIELDS.values()}, locale=row.locale, voting=voting
    )


@dataclass(slots=True)
class GuildEditState:
    row: ServerSetting
    values: GuildSettings


class GuildSettingsEdit(FieldCommand[SettingsEditUnitOfWork, GuildEditState, GuildSettings]):
    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

        async def load(uow: SettingsEditUnitOfWork, command: EditCommand) -> GuildEditState:
            row = await uow.settings.get_or_create_for_update(int(command.expected[0].target.key))
            return GuildEditState(row, await _guild_values(uow, row))

        async def flush(uow: SettingsEditUnitOfWork, state: GuildEditState, expected: Revision) -> Revision:
            return await uow.settings.flush_guild_revision(state.row, expected)

        async def authorize(uow: SettingsEditUnitOfWork, actor: Actor, state: GuildEditState) -> None:
            if actor.guild_id != state.row.server_id:
                raise AuthorizationError

        super().__init__(
            Aggregate("guild-settings", load, lambda state: state.row.revision, flush), GuildSettings, authorize
        )

    async def capture(self, uow: SettingsEditUnitOfWork, root: GuildEditState, command: EditCommand) -> GuildSettings:
        return root.values

    async def validate(
        self,
        uow: SettingsEditUnitOfWork,
        actor: Actor,
        root: GuildEditState,
        command: EditCommand,
        before: GuildSettings,
        after: GuildSettings,
    ) -> GuildSettings:
        guild_id = root.row.server_id
        changed = {name for name, value in before.model_dump().items() if value != after.model_dump()[name]}
        checked = changed or command.values.unpack().keys()
        nodes = set()
        if checked - {"voting"}:
            nodes.add(SETTINGS_SERVER_EDIT)
        if "voting" in checked:
            nodes.add(SETTINGS_VOTING_EDIT)
        for node in nodes:
            if not await self._permissions.allows_in(uow, actor, node):
                raise AuthorizationError
        for kind, configuration in after.voting.items():
            if configuration.preset is not None and (
                configuration.preset.guild_id != guild_id
                or configuration.preset.kind != kind
                or any(option.guild_id != guild_id for option in configuration.preset.options)
            ):
                message = "Vote emojis must belong to this guild and voting kind"
                raise ValidationError(message)
            if any(weight.guild_id != guild_id or weight.kind != kind for weight in configuration.weights):
                message = "Role weights must belong to this guild and voting kind"
                raise ValidationError(message)
        return after

    async def write(
        self, uow: SettingsEditUnitOfWork, root: GuildEditState, before: GuildSettings, after: GuildSettings
    ) -> None:
        guild_id = root.row.server_id
        for name in (*CHANNEL_FIELDS.values(), "locale"):
            setattr(root.row, name, getattr(after, name))
        for kind, configuration in after.voting.items():
            if configuration == before.voting[kind]:
                continue
            await uow.votes.reset_configuration(guild_id, kind)
            if configuration.preset is not None:
                await uow.votes.set_emoji_preset(configuration.preset)
            for weight in configuration.weights:
                await uow.votes.set_role_weight(weight)

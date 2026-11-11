"""Declarative runtime setting edits with current authority and aggregate revisions."""

from typing import Protocol

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.edits.application import EditUnitOfWork, FieldEditHandler
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT
from squid.persistence.revisions import require_revision
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
        actor: Actor,
        command: EditCommand,
        values: EditValues,
    ) -> EditValues:
        if actor.account_id is None or not await self._permissions.allows_in(uow, actor, SETTINGS_RUNTIME_EDIT):
            raise AuthorizationError
        return EditValues.of(RuntimeSettings.model_validate(values.unpack()).model_dump(mode="json"))

    async def apply(
        self,
        uow: SettingsEditUnitOfWork,
        actor: Actor,
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


GUILD_EDIT = "settings.guild"


def guild_target(guild_id: int) -> EditTarget:
    return EditTarget("guild-settings", str(guild_id))


async def guild_snapshot(uow: GuildSettingsUnitOfWork, guild_id: int) -> tuple[GuildSettings, int]:
    """Read channels and child vote settings under their shared root lock."""
    row = await uow.settings.get_or_create_for_update(guild_id)
    voting = {}
    for kind in VoteKind:
        voting[kind] = VoteConfiguration(
            await uow.votes.get_emoji_preset(guild_id, kind),
            tuple(sorted(await uow.votes.get_role_weights(guild_id, kind), key=lambda weight: weight.role_id)),
        )
    return GuildSettings(
        **{name: getattr(row, name) for name in CHANNEL_FIELDS.values()}, locale=row.locale, voting=voting
    ), row.revision


class GuildSettingsEdit(FieldEditHandler[SettingsEditUnitOfWork]):
    fields = frozenset(GuildSettings.model_fields)

    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    @staticmethod
    def guild_id(command: EditCommand) -> int:
        if len(command.expected) != 1 or command.expected[0].target.kind != "guild-settings":
            message = "A guild settings edit must guard its guild revision"
            raise ValueError(message)
        return int(command.expected[0].target.key)

    async def load(self, uow: SettingsEditUnitOfWork, command: EditCommand) -> EditValues:
        value, revision = await guild_snapshot(uow, self.guild_id(command))
        require_revision(command.expected[0], revision)
        return EditValues.of(value.model_dump(mode="json"))

    async def validate(
        self, uow: SettingsEditUnitOfWork, actor: Actor, command: EditCommand, values: EditValues
    ) -> EditValues:
        guild_id = self.guild_id(command)
        if actor.guild_id != guild_id:
            raise AuthorizationError
        before, _ = await guild_snapshot(uow, guild_id)
        after = GuildSettings.model_validate(values.unpack())
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
                raise ValueError(message)
            if any(weight.guild_id != guild_id or weight.kind != kind for weight in configuration.weights):
                message = "Role weights must belong to this guild and voting kind"
                raise ValueError(message)
        return EditValues.of(after.model_dump(mode="json"))

    async def apply(
        self, uow: SettingsEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        guild_id = self.guild_id(command)
        row = await uow.settings.get_or_create_for_update(guild_id)
        before = GuildSettings.model_validate(prepared.before.unpack())
        after = GuildSettings.model_validate(prepared.after.unpack())
        for name in (*CHANNEL_FIELDS.values(), "locale"):
            setattr(row, name, getattr(after, name))
        for kind, configuration in after.voting.items():
            if configuration == before.voting[kind]:
                continue
            await uow.votes.reset_configuration(guild_id, kind)
            if configuration.preset is not None:
                await uow.votes.set_emoji_preset(configuration.preset)
            for weight in configuration.weights:
                await uow.votes.set_role_weight(weight)
        return (await uow.settings.flush_guild_revision(row, command.expected[0]),)

"""Versioned configuration edits and reversible creation of unused starboards."""

from typing import Annotated, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from squid.edits.application import EditUnitOfWork, FieldEditHandler
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.persistence.revisions import require_revision
from squid.starboard.application.history import StarboardSnapshot
from squid.starboard.application.ports import StarboardUnitOfWork
from squid.starboard.domain import EDITABLE_SETTINGS, StarboardDirection
from squid.starboard.models import StarboardConfig, StarboardEmoji

STARBOARD_EDIT = "starboard.configure"
STARBOARD_CREATE = "starboard.create"
STARBOARD_DELETE = "starboard.delete"


def starboard_target(starboard_id: int) -> EditTarget:
    return EditTarget("starboard", str(starboard_id))


class StarboardEditUnitOfWork(StarboardUnitOfWork, EditUnitOfWork, PermissionUnitOfWork, Protocol):
    pass


class EmojiValues(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    emoji: str
    direction: StarboardDirection
    multiplier: Annotated[float, Field(gt=0)]
    position: int

    def row(self) -> StarboardEmoji:
        return StarboardEmoji(**self.model_dump())


class BoardValues(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    guild_id: int
    settings: dict[str, JsonValue]
    emojis: tuple[EmojiValues, ...]
    weights: dict[int, Annotated[float, Field(gt=0)]]

    @classmethod
    def capture(cls, snapshot: StarboardSnapshot) -> BoardValues:
        return cls(
            guild_id=snapshot.guild_id,
            settings=cast(dict[str, JsonValue], dict(snapshot.settings)),
            emojis=tuple(
                EmojiValues(emoji=emoji, direction=direction, multiplier=weight, position=position)
                for emoji, direction, weight, position in snapshot.emojis
            ),
            weights=dict(snapshot.weights),
        )

    def values(self) -> EditValues:
        return EditValues.of(self.model_dump(mode="json"))

    def config(self, starboard_id: int) -> StarboardConfig:
        if self.settings.keys() != EDITABLE_SETTINGS.keys():
            msg = "A starboard configuration must include exactly its editable settings."
            raise ValidationError(msg)
        for key, value in self.settings.items():
            valid = False
            match EDITABLE_SETTINGS[key]:
                case "boolean":
                    valid = isinstance(value, bool)
                case "integer":
                    valid = isinstance(value, int) and not isinstance(value, bool)
                case "threshold":
                    valid = isinstance(value, (int, float)) and not isinstance(value, bool)
                case "text":
                    valid = isinstance(value, str)
            if not valid:
                msg = f"Invalid value for starboard setting {key}."
                raise ValidationError(msg)
        row = StarboardConfig(
            id=starboard_id,
            guild_id=self.guild_id,
            name=str(self.settings["name"]),
            channel_id=int(str(self.settings["channel_id"])),
            emojis=tuple(emoji.row() for emoji in self.emojis),
        )
        for key, value in self.settings.items():
            setattr(row, key, value)
        row.name = row.name.strip()
        row.validate()
        return row


class BoardPresence(BoardValues):
    present: bool


class StarboardEdit(FieldEditHandler[StarboardEditUnitOfWork]):
    fields = frozenset(BoardValues.model_fields)

    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    @staticmethod
    def identity(command: EditCommand) -> int:
        if len(command.expected) != 1 or command.expected[0].target.kind != "starboard":
            msg = "A starboard edit must guard one board's revision."
            raise ValidationError(msg)
        return int(command.expected[0].target.key)

    async def row(
        self, uow: StarboardEditUnitOfWork, command: EditCommand, *, missing: bool = False
    ) -> StarboardConfig | None:
        row = await uow.starboards.get_by_id(self.identity(command), for_update=True, include_archived=True)
        require_revision(command.expected[0], 0 if row is None else row.revision)
        if row is None and not missing:
            msg = "Starboard not found."
            raise NotFoundError(msg)
        return row

    async def authorize(
        self, uow: StarboardEditUnitOfWork, actor: Actor, guild_id: int, nodes: set[PermissionNode]
    ) -> None:
        if actor.guild_id != guild_id:
            raise AuthorizationError
        for node in nodes:
            if not await self._permissions.allows_in(uow, actor, node):
                raise AuthorizationError

    @staticmethod
    async def capture(uow: StarboardEditUnitOfWork, row: StarboardConfig) -> BoardValues:
        return BoardValues.capture(StarboardSnapshot.capture(row, dict(await uow.starboards.role_multipliers(row.id))))

    async def load(self, uow: StarboardEditUnitOfWork, command: EditCommand) -> EditValues:
        row = await self.row(uow, command)
        assert row is not None
        if row.archived:
            msg = "Starboard not found."
            raise NotFoundError(msg)
        return (await self.capture(uow, row)).values()

    async def validate(
        self, uow: StarboardEditUnitOfWork, actor: Actor, command: EditCommand, values: EditValues
    ) -> EditValues:
        row = await self.row(uow, command)
        assert row is not None
        before = await self.capture(uow, row)
        after = BoardValues.model_validate(values.unpack())
        if after.guild_id != row.guild_id:
            msg = "A starboard edit cannot change its guild."
            raise ValidationError(msg)
        nodes = set()
        if before.settings != after.settings:
            nodes.add(STARBOARD_BOARD_EDIT)
        if before.emojis != after.emojis:
            nodes.add(STARBOARD_EMOJI_EDIT)
        if before.weights != after.weights:
            nodes.add(STARBOARD_WEIGHT_EDIT)
        await self.authorize(uow, actor, row.guild_id, nodes or {STARBOARD_BOARD_EDIT})
        normalized = after.config(row.id)
        await uow.starboards.lock_names(row.guild_id)
        collision = await uow.starboards.get(row.guild_id, normalized.name)
        if collision is not None and collision.id != row.id:
            msg = "A starboard with this name already exists."
            raise ConflictError(msg)
        return after.model_copy(update={"settings": after.settings | {"name": normalized.name}}).values()

    async def apply(
        self, uow: StarboardEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        row = await self.row(uow, command)
        assert row is not None
        before = BoardValues.model_validate(prepared.before.unpack())
        after = BoardValues.model_validate(prepared.after.unpack())
        for key, value in after.settings.items():
            setattr(row, key, value)
        if after.emojis != before.emojis:
            await uow.starboards.set_emojis(row.id, tuple(emoji.row() for emoji in after.emojis))
        for role_id in before.weights.keys() | after.weights.keys():
            if before.weights.get(role_id) != after.weights.get(role_id):
                await uow.starboards.set_role_multiplier(row.id, role_id, after.weights.get(role_id))
        return (await uow.starboards.flush_revision(row, command.expected[0]),)


class StarboardCreation(StarboardEdit):
    async def prepare(self, uow: StarboardEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedEdit:
        row = await self.row(uow, command, missing=True)
        after = BoardPresence.model_validate(command.values.unpack())
        normalized = after.config(self.identity(command))
        after = after.model_copy(update={"settings": after.settings | {"name": normalized.name}})
        await self.authorize(uow, actor, after.guild_id, {STARBOARD_BOARD_CREATE})
        if row is None:
            if not after.present:
                msg = "Starboard not found."
                raise NotFoundError(msg)
            before = after.model_copy(update={"present": False})
        else:
            before = BoardPresence(**(await self.capture(uow, row)).model_dump(), present=not row.archived)
            if before.model_copy(update={"present": after.present}) != after:
                msg = "The starboard configuration changed after creation."
                raise ConflictError(msg)
            if not after.present and not await uow.starboards.is_unused(row.id):
                msg = "This starboard is already in use; creation cannot be undone."
                raise ConflictError(msg)
        if after.present:
            await uow.starboards.lock_names(after.guild_id)
            collision = await uow.starboards.get(after.guild_id, normalized.name)
            if collision is not None and collision.id != self.identity(command):
                msg = "A starboard with this name already exists."
                raise ConflictError(msg)
        return PreparedEdit(before.values(), after.values())

    async def apply(
        self, uow: StarboardEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        row = await self.row(uow, command, missing=True)
        after = BoardPresence.model_validate(prepared.after.unpack())
        if row is None:
            row = after.config(self.identity(command))
            await uow.starboards.create_reserved(row)
            for role_id, weight in after.weights.items():
                await uow.starboards.set_role_multiplier(row.id, role_id, weight)
        else:
            row.archived = not after.present
        return (await uow.starboards.flush_revision(row, command.expected[0]),)


class StarboardDeletion(StarboardEdit):
    async def prepare(self, uow: StarboardEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedEdit:
        row = await self.row(uow, command)
        assert row is not None
        await self.authorize(uow, actor, row.guild_id, {STARBOARD_BOARD_DELETE})
        before = BoardPresence(**(await self.capture(uow, row)).model_dump(), present=not row.archived)
        return PreparedEdit(before.values(), before.model_copy(update={"present": False}).values(), final=True)

    async def apply(
        self, uow: StarboardEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        row = await self.row(uow, command)
        assert row is not None
        row.archived = True
        return (await uow.starboards.flush_revision(row, command.expected[0]),)

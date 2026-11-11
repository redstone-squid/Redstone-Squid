"""The consolidated starboard configuration workspace."""

from typing import Any, cast, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.starboard import StarboardCog
from squid.bot.starboard.screen import StarboardScreen
from squid.core.actors import Actor
from squid.edits.domain import EditAttempt, EditCommand, EditOutcome, EditReceipt, EditResult, EditValues, Revision
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.starboard.application import StarboardService
from squid.starboard.application.edits import STARBOARD_CREATE, STARBOARD_DELETE, BoardPresence, BoardValues
from squid.starboard.application.snapshots import StarboardSnapshot
from squid.starboard.models import StarboardConfig, StarboardEmoji
from squid_ui.runtime import transaction
from squid_ui.testing import labels


def board(name: str = "main") -> StarboardConfig:
    return StarboardConfig(
        id=1, guild_id=7, channel_id=11, name=name, emojis=(StarboardEmoji(emoji="⭐", direction="up"),)
    )


class FakeStarboardService(StarboardService):
    def __init__(self) -> None:
        self.config = board()
        self.list_reads = 0
        self.deletions: list[tuple[int, str]] = []

    @override
    async def reserve_id(self) -> int:
        return 2

    @override
    async def snapshot(self, starboard_id: int) -> StarboardSnapshot:
        return StarboardSnapshot.capture(self.config, {})

    @override
    async def list_for_guild(self, guild_id: int) -> tuple[StarboardConfig, ...]:
        del guild_id
        self.list_reads += 1
        return (self.config,)

    @override
    async def get(self, guild_id: int, name: str) -> StarboardConfig | None:
        del guild_id
        return self.config if name == self.config.name else None

    @override
    async def delete_starboard(self, guild_id: int, name: str) -> bool:
        self.deletions.append((guild_id, name))
        return True

    @override
    async def update_settings(self, guild_id: int, name: str, **settings: object) -> StarboardConfig | None:
        del guild_id, name, settings
        return self.config

    @override
    async def set_emojis(self, config: StarboardConfig, emojis: tuple[StarboardEmoji, ...]) -> None:
        del config, emojis

    @override
    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None:
        del config, role_id, multiplier


class ChannelValidator:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def __call__(self, channel_id: int) -> None:
        self.calls.append(channel_id)


class EditRecorder:
    def __init__(self, operations: FakeStarboardService) -> None:
        self.operations = operations
        self.commands: list[EditCommand] = []

    async def execute(self, command: EditCommand, actor: Actor, *, attempt: EditAttempt | None = None) -> EditResult:
        self.commands.append(command)
        if command.kind == STARBOARD_DELETE:
            await self.operations.delete_starboard(7, self.operations.config.name)
            before, after = EditValues.of({"present": True}), EditValues.of({"present": False})
        else:
            values = (
                BoardPresence.model_validate(command.values.unpack())
                if command.kind == STARBOARD_CREATE
                else BoardValues.model_validate(command.values.unpack())
            )
            self.operations.config = values.config(int(command.checked_revisions[0].target.key))
            before = values.model_copy(update={"present": False}).values()
            after = command.values
        revisions = tuple(Revision(version.target, version.value + 1) for version in command.checked_revisions)
        self.operations.config.revision = revisions[0].value
        receipt = EditReceipt(command, before, after, revisions, final=command.kind == STARBOARD_DELETE)
        if attempt is not None:
            attempt.receipt = receipt
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, revisions, changed=True, final=receipt.final
            ),
            receipt,
        )

    async def reconcile(self, command: EditCommand, actor: Actor, attempt: EditAttempt) -> EditResult | None:
        raise NotImplementedError


class NoticeEvent:
    def __init__(self, **values: object) -> None:
        self.values = values
        self.errors: tuple[sl.forms.FormIssue, ...] = ()
        self.notices: list[object] = []

    async def notice(self, text: object, **_kwargs: object) -> None:
        self.notices.append(text)


class TransitionEvent:
    def __init__(self, source: NoticeEvent) -> None:
        self.source = source


def make_screen(
    *,
    capabilities: frozenset[PermissionNode] = frozenset(),
    allowed: bool = True,
) -> tuple[StarboardScreen, FakeStarboardService, ChannelValidator]:
    operations = FakeStarboardService()
    creator = ChannelValidator()

    async def authorize(_node: PermissionNode) -> bool:
        return allowed

    async def actor() -> Actor:
        return Actor(account_id=1, guild_id=7)

    return (
        StarboardScreen(
            operations,
            guild_id=7,
            capabilities=capabilities,
            authorize=authorize,
            validate_channel=creator,
            edits=EditRecorder(operations),
            actor=actor,
        ),
        operations,
        creator,
    )


async def test_starboard_screen_uses_user_guild_tabs_and_browser() -> None:
    screen, _, _ = make_screen(
        capabilities=frozenset(
            {
                STARBOARD_BOARD_CREATE,
                STARBOARD_BOARD_EDIT,
                STARBOARD_BOARD_DELETE,
                STARBOARD_EMOJI_EDIT,
                STARBOARD_WEIGHT_EDIT,
            }
        )
    )
    await screen.on_load()

    assert isinstance(screen.__response_spec__.session, sd.SessionSpec)
    assert screen.__response_spec__.session.scope is sd.ScopeKind.USER_GUILD
    assert screen._browser is not None
    assert screen._tabs is not None
    assert {"Boards", "Create", "Settings", "Emojis", "Role weights"} <= set(labels(screen._tabs.render()))


async def test_create_uses_injected_discord_operation_and_refreshes() -> None:
    screen, operations, creator = make_screen(capabilities=frozenset({STARBOARD_BOARD_CREATE}))
    await screen.on_load()
    event = NoticeEvent(channel_id=11, name="main", required=4.0)

    with transaction():
        await screen._create(cast(sl.SubmitEvent, event))

    assert creator.calls == [11]
    assert operations.list_reads == 2
    assert len(event.notices) == 1


async def test_revoked_delete_permission_prevents_the_decision() -> None:
    screen, _, _ = make_screen(capabilities=frozenset({STARBOARD_BOARD_DELETE}), allowed=False)
    event = NoticeEvent(name="main")

    await screen._request_delete(cast(sl.SubmitEvent, event))

    assert screen._decision is None
    assert len(event.notices) == 1


async def test_delete_requires_confirmation_and_refreshes() -> None:
    screen, operations, _ = make_screen(capabilities=frozenset({STARBOARD_BOARD_DELETE}))
    await screen.on_load()
    submit = NoticeEvent(name="main")
    await screen._request_delete(cast(sl.SubmitEvent, submit))
    source = NoticeEvent()

    await screen._delete(cast(Any, TransitionEvent(source)), "confirm")

    assert operations.deletions == [(7, "main")]
    assert operations.list_reads == 2
    assert len(source.notices) == 1


def test_starboard_is_one_app_only_workspace() -> None:
    cog = cast(Any, StarboardCog)
    assert all(command.name != "starboard" for command in cog.__cog_commands__)
    assert "starboard" in {command.name for command in cog.__cog_app_commands__}

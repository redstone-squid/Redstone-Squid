"""Build edit command tests."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import discord

import squid_ui as sl
from squid.accounts.application import AccountService
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.ui.views import BuildEditScreen
from squid.builds.application import BuildService
from squid.builds.domain import DoorBuild, OtherBuild, RestrictionTypeLiteral, Status
from squid.permissions.application import PermissionService
from squid.permissions.domain import PermissionNode, Subject
from squid.settings.application import SettingsService
from squid.topics import resource_topic
from squid_ui_discord.testing import InteractionHarness, invoke_context_menu
from tests.support.discord import make_layout_bot


def _interaction(client: Any, *, user_id: int = 7) -> discord.Interaction[Any]:
    interaction = InteractionHarness(user_id=user_id).source
    interaction.client = client
    interaction.guild = None
    interaction.guild_id = None
    interaction.guild_locale = None
    interaction.locale = "en-US"
    return cast(discord.Interaction[Any], interaction)


class StubBuilds(BuildService):
    """The slice of BuildService the command touches."""

    def __init__(self, build: Any) -> None:
        self._build = build
        self.gets = 0
        self.sorted: list[list[str]] = []

    async def get(self, build_id: int) -> Any:
        self.gets += 1
        return self._build

    async def sort_restrictions(self, restrictions: Sequence[str]) -> dict[RestrictionTypeLiteral, list[str]]:
        self.sorted.append(list(restrictions))
        return {
            "wiring-placement": [name for name in restrictions if name == "Seamless"],
            "animated": [],
            "component": [name for name in restrictions if name == "Observerless"],
            "miscellaneous": [],
        }


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


class AccountRecorder(AccountService):
    def __init__(self) -> None:
        pass


class PermissionRecorder(PermissionService):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def allows(self, subject: Subject, node: PermissionNode | str) -> bool:
        return self.allowed


class AccountIdResolver:
    def __init__(self, account_id: int | None) -> None:
        self.account_id = account_id

    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        return self.account_id


class OwnerCheck:
    async def __call__(self, user: object) -> bool:
        return False


class BuildRenderer:
    async def render_container(self) -> discord.ui.Container:
        return discord.ui.Container(discord.ui.TextDisplay("card"))

    async def render_node(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        return sl.paragraph("card")


class Services:
    def __init__(self, allowed: bool) -> None:
        self.settings = SettingsRecorder()
        self.accounts = AccountRecorder()
        self.permissions = PermissionRecorder(allowed)


def _cog(build: Any, *, allowed: bool = True, account_id: int | None = 1) -> BuildEditCommands[Any]:
    cog = BuildEditCommands.__new__(BuildEditCommands)
    cog.builds = StubBuilds(build)
    cog.bot = cast(
        Any,
        make_layout_bot(
            services=Services(allowed),
            account_ids=AccountIdResolver(account_id),
            is_owner=OwnerCheck(),
            for_build=lambda _build: BuildRenderer(),
        ),
    )
    cog.ui = cog.bot.ui.scope(cog)
    return cog


async def _run(cog: BuildEditCommands[Any], **kwargs: Any) -> discord.Interaction[Any]:
    interaction = _interaction(cog.bot)
    await BuildEditCommands.edit_build(cog, interaction, build_id=1, **kwargs)
    return interaction


def _sent_view(interaction: discord.Interaction[Any]) -> Any:
    """The one message the command sent: it completes its own private defer in place."""
    source = cast(Any, interaction)
    source.followup.send.assert_not_awaited()
    return source.edit_original_response.await_args.kwargs["view"]


def _component(view: Any) -> BuildEditScreen | None:
    message_root = getattr(view, "_root", None)
    component = getattr(message_root, "component", None)
    return component if isinstance(component, BuildEditScreen) else None


def _staged(component: BuildEditScreen) -> dict[str, Any]:
    return {item.attribute: item.actual_value for item in component.items if item.modified}


def _door() -> DoorBuild:
    return DoorBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1)


async def test_typed_options_arrive_staged_in_the_workspace() -> None:
    """Typed options and the workspace end up in one review prompt."""
    cog = _cog(_door())

    component = _component(_sent_view(await _run(cog, door_size="2x2", creators="Alice, Bob", versions="1.21")))

    assert component is not None
    staged = _staged(component)
    assert staged["door_dimensions"] == (2, 2, None)
    assert staged["creators_ign"] == ["Alice", "Bob"]
    assert staged["version_spec"] == "1.21"


async def test_one_restrictions_option_is_sorted_into_its_buckets() -> None:
    """The restriction taxonomy is applied before the workspace opens."""
    cog = _cog(_door())

    component = _component(_sent_view(await _run(cog, restrictions="Seamless, Observerless")))

    assert component is not None
    staged = _staged(component)
    assert staged["wiring_placement_restrictions"] == ["Seamless"]
    assert staged["component_restrictions"] == ["Observerless"]


async def test_nothing_typed_still_opens_the_workspace() -> None:
    cog = _cog(_door())

    assert _component(_sent_view(await _run(cog))) is not None


async def test_a_stored_editor_follows_its_build_without_rereading_it() -> None:
    """The editor's resource is seeded with the build the command already fetched.

    It still declares the dependency, so the mount follows the topic -- but the follow costs
    a `sl.runtime.watch` line inside the loader rather than a second query to prime it.
    """
    cog = _cog(_door())

    interaction = await _run(cog)

    assert cast(StubBuilds, cog.builds).gets == 1
    component = _component(_sent_view(interaction))
    assert component is not None
    message_root = cast(Any, _sent_view(interaction))._root
    assert message_root.observed == (resource_topic("build", "1"),)


async def test_a_field_the_build_does_not_have_is_refused_not_dropped() -> None:
    """A field absent from the build is refused rather than silently dropped."""
    cog = _cog(OtherBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1))

    view = _sent_view(await _run(cog, door_size="2x2"))

    assert _component(view) is None


async def test_a_pending_builds_submitter_may_edit_it_without_the_node() -> None:
    """A pending submitter may open the editor without the node permission."""
    cog = _cog(_door(), allowed=False, account_id=1)

    assert _component(_sent_view(await _run(cog))) is not None


async def test_someone_else_without_the_node_is_denied() -> None:
    cog = _cog(_door(), allowed=False, account_id=99)

    assert _component(_sent_view(await _run(cog))) is None


async def test_a_missing_build_is_an_error_card() -> None:
    cog = _cog(None)

    assert _component(_sent_view(await _run(cog))) is None


class PostRecorder:
    def __init__(self, resource_key: str | None) -> None:
        self._resource_key = resource_key

    async def resolve(self, message_id: int) -> Any:
        if self._resource_key is None:
            return None

        @dataclass(frozen=True)
        class Post:
            resource_kind: str
            resource_key: str

        return Post("build", self._resource_key)


def _card(*, author_id: int) -> discord.Message:
    @dataclass(frozen=True)
    class Author:
        id: int

    @dataclass(frozen=True)
    class Message:
        id: int
        author: Author

    return cast(discord.Message, Message(id=55, author=Author(author_id)))


async def _open_from_menu(cog: BuildEditCommands[Any], message: discord.Message) -> discord.Interaction[Any]:
    interaction = _interaction(cog.bot)
    await invoke_context_menu(cog, cog.edit_context_menu, interaction, message)
    return interaction


async def test_the_menu_opens_the_editor_behind_its_own_defer() -> None:
    """`open_build_editor` used to resolve a second request for the same interaction, which
    did not know about the defer, so the workspace went out as a follow-up next to a
    placeholder that never resolved."""
    cog = _cog(_door())
    cog.bot.user = cast(Any, discord.Object(id=1))
    cog.bot.services.posts = PostRecorder("1")
    cog.bot.services.builds = cog.builds

    interaction = await _open_from_menu(cog, _card(author_id=1))

    assert _component(_sent_view(interaction)) is not None


async def test_the_menu_refuses_a_message_that_is_not_a_card() -> None:
    cog = _cog(_door())
    cog.bot.user = cast(Any, discord.Object(id=1))
    cog.bot.services.posts = PostRecorder(None)

    interaction = await _open_from_menu(cog, _card(author_id=1))

    assert _component(_sent_view(interaction)) is None

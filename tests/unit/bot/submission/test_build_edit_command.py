"""Build edit command tests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

import squid_layouts as sl
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.ui.views import BuildEditComponent
from squid.builds.domain import DoorBuild, OtherBuild, Status
from squid.topics import resource_topic
from squid_layouts.discord import SessionRegistry


class _Response:
    def __init__(self) -> None:
        self.deferred = False

    def is_done(self) -> bool:
        return self.deferred

    async def defer(self, **kwargs: Any) -> None:
        self.deferred = True


class _Followup:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return AsyncMock(spec=discord.Message)


def _interaction(client: Any, *, user_id: int = 7) -> discord.Interaction[Any]:
    return cast(
        discord.Interaction[Any],
        cast(
            Any,
            SimpleNamespace(
                client=client,
                user=SimpleNamespace(id=user_id),
                guild=None,
                guild_id=None,
                guild_locale=None,
                locale="en-US",
                response=_Response(),
                followup=_Followup(),
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                is_expired=lambda: False,
            ),
        ),
    )


class StubBuilds:
    """The slice of BuildService the command touches."""

    def __init__(self, build: Any) -> None:
        self._build = build
        self.gets = 0
        self.sorted: list[list[str]] = []

    async def get(self, build_id: int) -> Any:
        self.gets += 1
        return self._build

    async def sort_restrictions(self, restrictions: list[str]) -> dict[str, list[str]]:
        self.sorted.append(restrictions)
        return {
            "wiring-placement": [name for name in restrictions if name == "Seamless"],
            "animated": [],
            "component": [name for name in restrictions if name == "Observerless"],
            "miscellaneous": [],
        }


def _cog(build: Any, *, allowed: bool = True, account_id: int | None = 1) -> BuildEditCommands[Any]:
    cog = BuildEditCommands.__new__(BuildEditCommands)
    cog.builds = cast(Any, StubBuilds(build))
    topic_bus = sl.runtime.LocalTopicBus()
    layout_reactor = sl.discord.Reactor(topic_bus)
    cog.bot = cast(
        Any,
        SimpleNamespace(
            services=SimpleNamespace(
                settings=SimpleNamespace(),
                accounts=SimpleNamespace(),
                permissions=SimpleNamespace(allows=AsyncMock(return_value=allowed)),
            ),
            account_ids=SimpleNamespace(resolve=AsyncMock(return_value=account_id)),
            is_owner=AsyncMock(return_value=False),
            for_build=lambda _build: SimpleNamespace(
                render_container=AsyncMock(return_value=discord.ui.Container(discord.ui.TextDisplay("card"))),
                render_node=AsyncMock(return_value=sl.paragraph("card")),
            ),
            mounts=SessionRegistry(),
            topic_bus=topic_bus,
            layout_reactor=layout_reactor,
        ),
    )
    return cog


async def _run(cog: BuildEditCommands[Any], **kwargs: Any) -> discord.Interaction[Any]:
    interaction = _interaction(cog.bot)
    await BuildEditCommands.edit_build.callback(cog, interaction, build_id=1, **kwargs)
    return interaction


def _sent_view(interaction: discord.Interaction[Any]) -> Any:
    return cast(Any, interaction).followup.sent[-1]["view"]


def _component(view: Any) -> BuildEditComponent | None:
    mount = getattr(view, "_mount", None)
    component = getattr(mount, "component", None)
    return component if isinstance(component, BuildEditComponent) else None


def _staged(component: BuildEditComponent) -> dict[str, Any]:
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
    mount = cast(Any, _sent_view(interaction))._mount
    assert mount.observed == (resource_topic("build", "1"),)


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

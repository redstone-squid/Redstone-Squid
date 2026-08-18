"""`/build edit`: typed options in front, the workspace behind them, one gate for both."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.ui.views import BuildEditView
from squid.builds.domain import DoorBuild, OtherBuild, Status


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
            ),
        ),
    )


class StubBuilds:
    """The slice of BuildService the command touches."""

    def __init__(self, build: Any) -> None:
        self._build = build
        self.sorted: list[list[str]] = []

    async def get(self, build_id: int) -> Any:
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
                render_container=AsyncMock(return_value=discord.ui.Container(discord.ui.TextDisplay("card")))
            ),
        ),
    )
    return cog


async def _run(cog: BuildEditCommands[Any], **kwargs: Any) -> discord.Interaction[Any]:
    interaction = _interaction(cog.bot)
    await BuildEditCommands.edit_build.callback(cog, interaction, build_id=1, **kwargs)  # type: ignore[arg-type]
    return interaction


def _sent_view(interaction: discord.Interaction[Any]) -> Any:
    return cast(Any, interaction).followup.sent[-1]["view"]


def _staged(view: BuildEditView[Any]) -> dict[str, Any]:
    return {item.attribute: item.actual_value for item in view.items if item.modified}


def _door() -> DoorBuild:
    return DoorBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1)


async def test_typed_options_arrive_staged_in_the_workspace() -> None:
    """The whole point of the merge: options carry autocomplete, the workspace carries the rest,
    and both end up in one review prompt rather than in two commands."""
    cog = _cog(_door())

    view = _sent_view(await _run(cog, door_size="2x2", creators="Alice, Bob", versions="1.21"))

    assert isinstance(view, BuildEditView)
    staged = _staged(view)
    assert staged["door_dimensions"] == (2, 2, None)
    assert staged["creators_ign"] == ["Alice", "Bob"]
    assert staged["version_spec"] == "1.21"


async def test_one_restrictions_option_is_sorted_into_its_buckets() -> None:
    """Which bucket a restriction belongs in is a fact about the restriction, so the person
    editing gives one list and the taxonomy splits it -- as `/build submit` already does."""
    cog = _cog(_door())

    view = _sent_view(await _run(cog, restrictions="Seamless, Observerless"))

    staged = _staged(view)
    assert staged["wiring_placement_restrictions"] == ["Seamless"]
    assert staged["component_restrictions"] == ["Observerless"]


async def test_nothing_typed_still_opens_the_workspace() -> None:
    cog = _cog(_door())

    assert isinstance(_sent_view(await _run(cog)), BuildEditView)


async def test_a_field_the_build_does_not_have_is_refused_not_dropped() -> None:
    """Silently ignoring a typed option is the failure this command was merged to end."""
    cog = _cog(OtherBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1))

    view = _sent_view(await _run(cog, door_size="2x2"))

    assert not isinstance(view, BuildEditView)


async def test_a_pending_builds_submitter_may_edit_it_without_the_node() -> None:
    """The command was gated on `build.submission.edit` while the card's Edit button admitted
    the submitter too, so the same operation had two answers (audit, `/build`)."""
    cog = _cog(_door(), allowed=False, account_id=1)

    assert isinstance(_sent_view(await _run(cog)), BuildEditView)


async def test_someone_else_without_the_node_is_denied() -> None:
    cog = _cog(_door(), allowed=False, account_id=99)

    assert not isinstance(_sent_view(await _run(cog)), BuildEditView)


async def test_a_missing_build_is_an_error_card() -> None:
    cog = _cog(None)

    assert not isinstance(_sent_view(await _run(cog)), BuildEditView)

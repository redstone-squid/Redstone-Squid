"""Open and recover existing build editors through their current routes.

Retained submission-draft editing is covered in ``test_draft_editor.py``.
"""

from typing import Any, cast, override

import discord
import pytest

import squid_ui.ext.discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider
from squid.accounts.models import Account
from squid.bot.routes import AppRequest
from squid.bot.submission.ui.build_edit import BuildEditScreen
from squid.bot.submission.ui.controls import edit_build, recover_build_editor
from squid.bot.submission.ui.opening import prepare_build_editor
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.application import BuildService
from squid.builds.domain import Status
from squid.builds.models import DoorBuild
from squid.core.actors import Actor
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain import CheckMode, Decision, PermissionCheck, PermissionNode, Reason
from squid.settings.application import SettingsService
from squid.submissions.application import FormOptionSet, SubmissionFormService
from squid.submissions.application.forms import build_edit_manifest
from squid.submissions.domain import FormManifest
from squid_ui.ext.discord.testing import InteractionHarness
from squid_ui.routing import Endpoint
from squid_ui.testing import RecordingResponder, press_event
from tests.support.build_edit_runner import EDIT_OPTIONS
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


def _interaction(client: Any, *, user_id: int = 7) -> discord.Interaction[Any]:
    interaction = InteractionHarness(user_id=user_id).source
    interaction.client = client
    interaction.guild = None
    interaction.guild_id = None
    interaction.guild_locale = None
    interaction.locale = "en-US"
    return cast(discord.Interaction[Any], interaction)


class StubBuilds(BuildService):
    """The build lookup used by editor routes."""

    def __init__(self, build: Any) -> None:
        self._build = build
        self.gets = 0

    @override
    async def get(self, build_id: int) -> Any:
        self.gets += 1
        return self._build


class Forms(SubmissionFormService):
    def __init__(self) -> None:
        pass

    @override
    async def edit_manifest(self, *, locale: str | None) -> FormManifest:
        return build_edit_manifest()

    @override
    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        return FormOptionSet(source, category, 1, EDIT_OPTIONS.get(source, ()))


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    @override
    async def get_locale(self, server_id: int) -> str | None:
        return None


class AccountRecorder(AccountService):
    def __init__(self, account_id: int | None) -> None:
        self.account_id = account_id

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return None if self.account_id is None else Account(id=self.account_id)


class PermissionRecorder(PermissionService):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    @override
    async def check(
        self,
        subject: Actor,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> PermissionCheck:
        return PermissionCheck(
            tuple(Decision(str(node), allowed=self.allowed, reason=Reason.DEFAULT) for node in nodes), mode=mode
        )


class AccountIdResolver:
    def __init__(self, account_id: int | None) -> None:
        self.account_id = account_id

    async def resolve(self, accounts: AccountService, discord_id: int) -> int | None:
        return self.account_id


class OwnerCheck:
    async def __call__(self, user: object) -> bool:
        return False


def _bot(build: Any, *, allowed: bool = True, account_id: int | None = 1) -> Any:
    return make_layout_bot(
        services=bot_services(
            settings=SettingsRecorder(),
            accounts=AccountRecorder(account_id),
            permissions=PermissionRecorder(allowed),
            builds=StubBuilds(build),
            submission_forms=Forms(),
        ),
        account_ids=AccountIdResolver(account_id),
        is_owner=OwnerCheck(),
        media_previews=MediaPreviewClient(),
    )


async def _run(bot: Any) -> discord.Interaction[Any]:
    interaction = _interaction(bot)
    await edit_build(await sd.request(interaction), 1)
    return interaction


def _sent_view(interaction: discord.Interaction[Any]) -> Any:
    """Read the view from the initial send, follow-up, or original-response edit."""
    source = cast(Any, interaction)
    call = (
        source.response.send_message.await_args
        or source.followup.send.await_args
        or source.edit_original_response.await_args
    )
    return call.kwargs["view"]


def _component(view: Any) -> BuildEditScreen | None:
    message_root = getattr(view, "_root", None)
    component = getattr(message_root, "component", None)
    return component if isinstance(component, BuildEditScreen) else None


def _door() -> DoorBuild:
    return DoorBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1)


async def test_edit_route_opens_the_workspace() -> None:
    bot = _bot(_door())

    assert _component(_sent_view(await _run(bot))) is not None


async def test_a_stored_editor_follows_its_build_without_rereading_it() -> None:
    """Reuse the route's fetched build for the first render, then follow later build updates.

    The editor must register its topic dependency even when its initial value needs no query.
    """
    bot = _bot(_door())

    interaction = await _run(bot)

    assert cast(StubBuilds, bot.services.builds).gets == 1
    component = _component(_sent_view(interaction))
    assert component is not None
    message_root = cast(Any, _sent_view(interaction))._root
    assert bot.topic_bus.topic("build", "1") in message_root.observed


async def test_a_pending_builds_submitter_may_edit_it_without_the_node() -> None:
    bot = _bot(_door(), allowed=False, account_id=1)

    assert _component(_sent_view(await _run(bot))) is not None


async def test_someone_else_without_the_node_is_denied() -> None:
    bot = _bot(_door(), allowed=False, account_id=99)

    assert _component(_sent_view(await _run(bot))) is None


async def test_a_missing_build_is_an_error_card() -> None:
    bot = _bot(None)

    assert _component(_sent_view(await _run(bot))) is None


async def test_recovery_route_reloads_current_state_before_authorizing() -> None:
    latest = DoorBuild(
        id=1,
        revision=4,
        submission_status=Status.PENDING,
        submitter_account_id=1,
        version_spec="1.21.4",
    )
    bot = _bot(latest, allowed=False, account_id=1)
    interaction = _interaction(bot)

    await recover_build_editor(await sd.request(interaction), 1)

    component = _component(_sent_view(interaction))
    assert component is not None
    assert component.build is latest
    assert cast(StubBuilds, bot.services.builds).gets == 1
    assert "Unsaved changes from the previous editor were discarded" in str(component.render())


async def test_recovery_route_denies_an_actor_after_reloading_current_state() -> None:
    latest = DoorBuild(id=1, revision=4, submission_status=Status.CONFIRMED, submitter_account_id=1)
    bot = _bot(latest, allowed=False, account_id=1)
    interaction = _interaction(bot)

    await recover_build_editor(await sd.request(interaction), 1)

    assert _component(_sent_view(interaction)) is None
    assert cast(StubBuilds, bot.services.builds).gets == 1


async def test_in_session_reload_reauthorizes_the_reloaded_non_pending_build() -> None:
    original = _door()
    latest = DoorBuild(id=1, revision=2, submission_status=Status.CONFIRMED, submitter_account_id=1)
    bot = _bot(latest, allowed=False, account_id=1)
    interaction = _interaction(bot)
    request = await sd.request(interaction)
    screen = await prepare_build_editor(request, original, cast(BuildService, bot.services.builds))
    await screen.projection.reload()
    responder = RecordingResponder()

    await screen._reload(press_event(responder=responder))

    assert screen.build is latest
    assert responder.acknowledged == 0
    assert responder.notices[0][0] == "Only the pending build's submitter or a trusted staff member can edit it."


@pytest.mark.parametrize("endpoint", [edit_build, recover_build_editor])
async def test_editor_routes_explain_a_deleted_build(endpoint: Endpoint[AppRequest, ...]) -> None:
    bot = _bot(None)
    interaction = _interaction(bot)

    await endpoint(await sd.request(interaction), 1)

    assert _component(_sent_view(interaction)) is None
    assert cast(StubBuilds, bot.services.builds).gets == 1

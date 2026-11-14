"""Open an existing build's editor with staged changes and current authorization.

Retained submission-draft editing is covered in ``test_draft_editor.py``.
"""

from typing import Any, cast, override

import discord

import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider
from squid.accounts.models import Account
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.ui.build_edit import BuildEditScreen
from squid.bot.submission.ui.controls import recover_build_editor
from squid.bot.submission.ui.opening import prepare_build_editor
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.application import BuildService
from squid.builds.domain import Status
from squid.builds.domain.credits import name_credits
from squid.builds.models import DoorBuild, OtherBuild
from squid.core.actors import Actor
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain import CheckMode, Decision, PermissionCheck, PermissionNode, Reason
from squid.settings.application import SettingsService
from squid.submissions.application import FormOptionSet, SubmissionFormService
from squid.submissions.application.forms import build_edit_manifest
from squid.submissions.domain import FormManifest
from squid_ui.testing import RecordingResponder, press_event
from squid_ui_discord.testing import InteractionHarness
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
    """The slice of BuildService the command touches."""

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


def _cog(build: Any, *, allowed: bool = True, account_id: int | None = 1) -> BuildEditCommands[Any]:
    cog = BuildEditCommands.__new__(BuildEditCommands)
    builds = StubBuilds(build)
    cog.builds = builds
    doubles: dict[str, object] = {
        "settings": SettingsRecorder(),
        "accounts": AccountRecorder(account_id),
        "permissions": PermissionRecorder(allowed),
        "builds": builds,
        "submission_forms": Forms(),
    }
    services = bot_services(**doubles)
    cog.bot = cast(
        Any,
        make_layout_bot(
            services=services,
            account_ids=AccountIdResolver(account_id),
            is_owner=OwnerCheck(),
            media_previews=MediaPreviewClient(),
        ),
    )
    cog.ui = cog.bot.ui.scope(cog)
    return cog


async def _run(cog: BuildEditCommands[Any], **kwargs: Any) -> discord.Interaction[Any]:
    interaction = _interaction(cog.bot)
    await BuildEditCommands.edit_build(cog, interaction, build_id=1, **kwargs)
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


def _staged(component: BuildEditScreen) -> dict[str, Any]:
    workspace = component.workspace
    return {field_id: workspace.staged.get(field_id) for field_id in workspace.changed_fields()}


def _door() -> DoorBuild:
    return DoorBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1)


async def test_typed_options_arrive_staged_in_the_workspace() -> None:
    """Typed options and the workspace end up in one review prompt."""
    cog = _cog(_door())

    component = _component(_sent_view(await _run(cog, door_size="2x2", creators="Alice, Bob", versions="1.21")))

    assert component is not None
    assert _staged(component) == {
        "opening_width": 2,
        "opening_height": 2,
        "credits": [credit.to_json() for credit in name_credits(["Alice", "Bob"])],
        "version_compatibility": "1.21",
    }


async def test_restriction_and_pattern_names_become_catalogue_choices() -> None:
    cog = _cog(_door())

    component = _component(_sent_view(await _run(cog, restrictions="seamless, Observerless", pattern="Full Lamp")))

    assert component is not None
    assert _staged(component) == {"restrictions": ["seamless", "observerless"], "patterns": ["full_lamp"]}


async def test_a_name_outside_the_catalogue_is_refused() -> None:
    cog = _cog(_door())

    assert _component(_sent_view(await _run(cog, restrictions="Seamless, Hand-wired"))) is None


async def test_nothing_typed_still_opens_the_workspace() -> None:
    cog = _cog(_door())

    assert _component(_sent_view(await _run(cog))) is not None


async def test_a_stored_editor_follows_its_build_without_rereading_it() -> None:
    """Reuse the command's fetched build for the first render, then follow later build updates.

    The editor must register its topic dependency even when its initial value needs no query.
    """
    cog = _cog(_door())

    interaction = await _run(cog)

    assert cast(StubBuilds, cog.builds).gets == 1
    component = _component(_sent_view(interaction))
    assert component is not None
    message_root = cast(Any, _sent_view(interaction))._root
    assert cog.bot.topic_bus.topic("build", "1") in message_root.observed


async def test_a_field_the_build_does_not_have_is_refused_not_dropped() -> None:
    cog = _cog(OtherBuild(id=1, submission_status=Status.PENDING, submitter_account_id=1))

    view = _sent_view(await _run(cog, door_size="2x2"))

    assert _component(view) is None


async def test_a_pending_builds_submitter_may_edit_it_without_the_node() -> None:
    cog = _cog(_door(), allowed=False, account_id=1)

    assert _component(_sent_view(await _run(cog))) is not None


async def test_someone_else_without_the_node_is_denied() -> None:
    cog = _cog(_door(), allowed=False, account_id=99)

    assert _component(_sent_view(await _run(cog))) is None


async def test_a_missing_build_is_an_error_card() -> None:
    cog = _cog(None)

    assert _component(_sent_view(await _run(cog))) is None


async def test_recovery_route_reloads_current_state_before_authorizing() -> None:
    latest = DoorBuild(
        id=1,
        revision=4,
        submission_status=Status.PENDING,
        submitter_account_id=1,
        version_spec="1.21.4",
    )
    cog = _cog(latest, allowed=False, account_id=1)
    interaction = _interaction(cog.bot)

    await recover_build_editor(interaction, 1)

    component = _component(_sent_view(interaction))
    assert component is not None
    assert component.build is latest
    assert cast(StubBuilds, cog.builds).gets == 1
    assert "Unsaved changes from the previous editor were discarded" in str(component.render())


async def test_recovery_route_denies_an_actor_after_reloading_current_state() -> None:
    latest = DoorBuild(id=1, revision=4, submission_status=Status.CONFIRMED, submitter_account_id=1)
    cog = _cog(latest, allowed=False, account_id=1)
    interaction = _interaction(cog.bot)

    await recover_build_editor(interaction, 1)

    assert _component(_sent_view(interaction)) is None
    assert cast(StubBuilds, cog.builds).gets == 1


async def test_in_session_reload_reauthorizes_the_reloaded_non_pending_build() -> None:
    original = _door()
    latest = DoorBuild(id=1, revision=2, submission_status=Status.CONFIRMED, submitter_account_id=1)
    cog = _cog(latest, allowed=False, account_id=1)
    interaction = _interaction(cog.bot)
    request = await sd.request(interaction)
    screen = await prepare_build_editor(request, original, cast(BuildService, cog.builds))
    await screen.projection.reload()
    responder = RecordingResponder()

    await screen._reload(press_event(responder=responder))

    assert screen.build is latest
    assert responder.acknowledged == 0
    assert responder.notices[0][0] == "Only the pending build's submitter or a trusted staff member can edit it."

"""Command wrappers preserve owner scopes, access checks, and consent responses."""

from collections.abc import Sequence
from typing import Any, cast

import pytest

import squid_ui_discord as sd
from squid.bot import diagnostics, notifications
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.models import ErrorReport
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_READ
from squid_ui_discord.testing import AsyncCallRecorder, interaction_harness
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


class Reports(ErrorReportService):
    def __init__(self) -> None:
        self.recent_calls: AsyncCallRecorder = AsyncCallRecorder(result=())

    async def recent(self, *, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        return await self.recent_calls(limit=limit, work_lost_only=work_lost_only)


@pytest.mark.parametrize("allowed", [True, False])
async def test_error_command_checks_access_before_presenting_in_its_cog_scope(
    monkeypatch: pytest.MonkeyPatch, allowed: bool
) -> None:
    reports = Reports()
    reports.recent_calls.return_value = ()
    bot = make_layout_bot(services=bot_services(error_reports=reports))
    cog = diagnostics.Diagnostics(bot)
    interaction = interaction_harness(user_id=7)
    interaction.client = bot
    check = AsyncCallRecorder(error=None if allowed else PermissionNodeRequired((DIAGNOSTICS_ERROR_READ.name,)))
    monkeypatch.setattr(diagnostics, "enforce", check)
    monkeypatch.setattr(diagnostics, "allows", AsyncCallRecorder(result=False))

    if allowed:
        await cast(Any, cog.errors).callback(cog, interaction, None)
    else:
        with pytest.raises(PermissionNodeRequired):
            await cast(Any, cog.errors).callback(cog, interaction, None)

    request = await cog.ui.request(interaction)
    check.assert_awaited_once_with(request, DIAGNOSTICS_ERROR_READ)
    assert request.owner is cog
    if allowed:
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        (session,) = bot.sessions.active()
        (root,) = session.message_roots
        assert root.component.opening is request
        await cog.ui.close()
        assert root.finished
    else:
        reports.recent_calls.assert_not_awaited()
        interaction.response.send_message.assert_not_awaited()
        assert tuple(bot.sessions.active()) == ()


async def test_notification_consent_uses_the_command_request_without_a_second_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_layout_bot(services=bot_services())
    accounts = bot.services.accounts
    cog = notifications.NotificationCog(bot)
    interaction = interaction_harness(user_id=7)
    interaction.client = bot
    seen: list[sd.Request[Any]] = []

    async def refuse(request: sd.Request[Any], service: object) -> None:
        assert service is accounts
        seen.append(request)
        await request.respond("Consent required", audience="personal")

    monkeypatch.setattr(notifications, "ensure_consented_account", refuse)
    await cast(Any, cog.notifications).callback(cog, interaction)

    request = await cog.ui.request(interaction)
    assert seen == [request]
    assert request.owner is cog
    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()
    assert tuple(bot.sessions.active()) == ()

from typing import override

import pytest
import wrapture

from squid.bot.accounts.consent import ConsentPrompt
from squid.core.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, IdentityProvider
from squid.core.accounts.models import Account
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import WorkspaceAccounts, WorkspaceInbox
from tests.support.services import bot_services
from tests.support.submission_form import Drafts, Finalization, Forms, Intake, Schematics


class FirstTimeAccounts(WorkspaceAccounts):
    @override
    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        assert provider is IdentityProvider.DISCORD
        assert consent is not None
        assert subject not in self.discord_accounts
        account = Account(id=37, consent=consent)
        self.discord_accounts[subject] = account
        return account


@pytest.mark.parametrize("answer", ["Agree", "Cancel"])
async def test_first_submission_waits_for_consent_before_saving(answer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = FirstTimeAccounts()
    drafts = Drafts()
    services = bot_services(
        accounts=accounts,
        submission_drafts=drafts,
        submission_forms=Forms(),
        submission_intake=Intake(),
        schematics=Schematics(),
        submission_finalization=Finalization(),
    )
    async with command_environment("squid.bot.submission.cog", services=services) as (_, env):
        # Only this wait needs another member action. Database and rendering work must settle normally.
        wait = ConsentPrompt.wait
        monkeypatch.setattr(
            ConsentPrompt, "wait", lambda prompt: env.external_wait(wait(prompt), reason="member consent")
        )
        channel = env.guild.create_text_channel("submissions")
        alice = env.guild.add_member(env.create_user("Alice"))

        opened = await alice.slash(channel, "build submit", name="Alice's door")
        assert opened.response is not None
        assert opened.response.ephemeral
        assert not accounts.discord_accounts
        assert drafts.current is None

        decision = await alice.click(opened.response.message, label=answer)
        assert decision.acknowledged
        if answer == "Cancel":
            assert not accounts.discord_accounts
            assert drafts.current is None
            assert any(
                "Cancelled. No account information was stored." in displayed_text(msg)
                for msg in channel.history(viewer=alice)
            )
        else:
            consent = accounts.discord_accounts[str(alice.id)].consent
            assert consent is not None
            assert consent.version == CURRENT_CONSENT_VERSION
            assert drafts.current is not None
            assert drafts.current.snapshot.owner_account_id == 37
            assert drafts.current.snapshot.answers["display_name"] == "Alice's door"
            (editor,) = opened.followups
            assert editor.ephemeral
            assert any("Alice's door" in text for text in displayed_text(editor.message))


async def test_declining_notifications_consent_leaves_no_workspace() -> None:
    accounts = FirstTimeAccounts()
    services = bot_services(accounts=accounts, notifications=WorkspaceInbox(37))
    async with command_environment("squid.bot.notifications_cog", services=services) as (bot, env):
        channel = env.guild.create_text_channel("community")
        visitor = env.guild.add_member(env.create_user("visitor"))
        wait = wrapture.binding(ConsentPrompt, "wait").on_call.decorates(
            lambda wrapped, _instance, args, kwargs: env.external_wait(
                wrapped(*args, **kwargs), reason="member consent"
            )
        )

        with wait:
            opened = await visitor.slash(channel, "notifications")
            assert opened.response is not None
            assert opened.response.ephemeral
            assert not opened.followups
            decision = await visitor.click(opened.response.message, label="Cancel")

        assert decision.acknowledged
        assert not accounts.discord_accounts
        assert not tuple(bot.sessions.active())
        contents = [text for message in channel.history(viewer=visitor) for text in displayed_text(message)]
        assert "Cancelled. No account information was stored." in contents

"""The Discord consent gate: ask first, then continue the requested action.

Commands can await a prompt; an active panel instead resumes through a continuation so other
presses remain available while the reader decides.
"""

from dataclasses import dataclass
from typing import Any, override
from uuid import UUID

import anyio
import discord
import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    PRIVACY_NOTICE,
    AccountConsent,
    CreditPreview,
    IdentityProvider,
    LinkPreview,
)
from squid.accounts.models import Account, AccountIdentity
from squid.bot.consent import (
    ConsentPrompt,
    _consent_prompt,
    ensure_consented_account,
    prompt_for_consent,
    request_consent,
)
from squid.core.sentinel import NOT_ASKED
from squid_ui.testing import RecordingResponder, press
from squid_ui.text import NEUTRAL, resolve_text
from squid_ui_discord import Everyone, SessionKey
from squid_ui_discord.sessions import Opened
from squid_ui_discord.testing import (
    ContextHarness,
    InteractionHarness,
    MessageHarness,
    commit_render,
    delivered_to,
    interaction_harness,
    message_harness,
)
from tests.support.discord import make_layout_bot

AFTER_CUTOFF = Instant.from_utc(2026, 8, 5)
USER_ID = 123
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")


def discord_account(*, account_id: int = 7, consented: bool) -> Account:
    return Account(
        id=account_id,
        created_at=AFTER_CUTOFF,
        identities=(AccountIdentity.discord(USER_ID),),
        consent=AccountConsent(CURRENT_CONSENT_VERSION, AFTER_CUTOFF) if consented else None,
    )


@dataclass(frozen=True)
class IdentityCreation:
    provider: IdentityProvider
    subject: str
    consent: AccountConsent | None


class AccountRecorder(AccountService):
    def __init__(self, existing: Account | None) -> None:
        self.existing = existing
        self.identity_reads: list[tuple[IdentityProvider, str]] = []
        self.identity_creations: list[IdentityCreation] = []

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        self.identity_reads.append((provider, subject))
        return self.existing

    @override
    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        self.identity_creations.append(IdentityCreation(provider, subject, consent))
        return discord_account(consented=True)


def make_accounts(existing: Account | None) -> AccountRecorder:
    return AccountRecorder(existing)


def make_context() -> Any:
    """A prefix/hybrid context with recording message delivery."""
    return ContextHarness(message=MessageHarness(message_id=1), bot=make_layout_bot(), user_id=USER_ID).source


def make_interaction(*, response_done: bool) -> Any:
    interaction = InteractionHarness(USER_ID, message_id=1, client=make_layout_bot())
    interaction.response._done = response_done
    interaction.followup.send.result = MessageHarness(message_id=1)
    return interaction.source


async def gate(source: Any) -> sd.Request[Any]:
    """The request a command hands the gate."""
    return await sd.request(source)


@pytest.mark.parametrize(
    ("credit", "expected_credit"),
    [
        pytest.param(None, "No build credits", id="none"),
        pytest.param(CreditPreview("Notch", 3), "becomes attributed", id="unclaimed"),
        pytest.param(CreditPreview("Notch", 3, UUID(int=9)), "opens a claim", id="contested"),
    ],
)
def test_link_consent_card_names_every_stored_value_and_credit_outcome(
    credit: CreditPreview | None,
    expected_credit: str,
) -> None:
    prompt = _consent_prompt(
        user_id=USER_ID,
        preview=LinkPreview(JAVA_UUID, "Notch", credit),
    )
    root = sd.MessageRoot(prompt, access=sd.Owner(USER_ID), timeout=120)

    payload = commit_render(root).to_components()
    rendered = str(payload)
    card_text = "\n".join(component["content"] for component in payload[0]["components"])

    assert payload[0]["type"] == discord.ComponentType.container.value
    assert "Discord user ID" in card_text
    assert "Minecraft UUID" in card_text
    assert "Minecraft username" in card_text
    assert "Notch" in card_text
    assert str(JAVA_UUID) in card_text
    assert expected_credit in card_text
    assert CURRENT_CONSENT_VERSION in card_text
    assert all(label in rendered for label in ("Agree and link", "Cancel", "Privacy notice"))


async def test_cancel_and_privacy_never_record_consent() -> None:
    cancel_prompt = _consent_prompt(user_id=USER_ID, preview=LinkPreview(JAVA_UUID, "Notch"))
    async with sd.testing.stage(cancel_prompt, owner=object(), user_id=USER_ID) as ui:
        await ui.press("cancel")
        outcome = await cancel_prompt.wait()
        assert outcome == sd.Cancelled(sd.ScreenCancelReason.USER)
        assert cancel_prompt.consent is None
        assert ui.root.finished

    privacy_prompt = _consent_prompt(user_id=USER_ID, preview=LinkPreview(JAVA_UUID, "Notch"))
    privacy_responder = RecordingResponder()
    await press(privacy_prompt, "privacy", responder=privacy_responder)

    assert privacy_prompt.consent is None
    assert privacy_prompt.outcome is None
    assert privacy_responder.notices == [
        (resolve_text(PRIVACY_NOTICE, NEUTRAL).content, sl.interactions.Visibility.PRIVATE)
    ]


async def test_an_already_consented_user_is_never_prompted() -> None:
    """An existing current receipt avoids prompting or creating an account."""
    accounts = make_accounts(discord_account(consented=True))
    ctx = make_context()

    assert await ensure_consented_account(await gate(ctx), accounts) == 7
    ctx.send.assert_not_awaited()
    assert accounts.identity_creations == []


async def test_declining_stores_nothing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for consent must not create an account before acceptance."""
    accounts = make_accounts(None)
    ctx = make_context()
    _stub_prompt(monkeypatch, agree=False)

    assert await ensure_consented_account(await gate(ctx), accounts) is None
    assert accounts.identity_creations == []
    assert ctx.send.records  # The user is told, rather than left with a silent no-op.


async def test_agreeing_mints_the_account_and_its_receipt_in_one_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating the row and recording consent separately leaves a receipt-less account behind
    whenever the second call fails."""
    accounts = make_accounts(None)
    ctx = make_context()
    _stub_prompt(monkeypatch, agree=True)

    assert await ensure_consented_account(await gate(ctx), accounts) == 7

    assert len(accounts.identity_creations) == 1
    creation = accounts.identity_creations[0]
    assert (creation.provider, creation.subject) == (IdentityProvider.DISCORD, str(USER_ID))
    assert creation.consent is not None
    assert creation.consent.version == CURRENT_CONSENT_VERSION


async def test_a_stale_receipt_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = make_accounts(Account(id=7, created_at=AFTER_CUTOFF, consent=AccountConsent("1970-01-01", AFTER_CUTOFF)))
    ctx = make_context()
    _stub_prompt(monkeypatch, agree=True)

    assert await ensure_consented_account(await gate(ctx), accounts) == 7
    assert len(accounts.identity_creations) == 1


@pytest.mark.parametrize("response_done", [False, True], ids=["fresh", "deferred"])
async def test_the_gate_works_from_an_interaction_in_either_response_state(
    monkeypatch: pytest.MonkeyPatch, response_done: bool
) -> None:
    """Slash cogs, modals and view buttons all reach the gate, and they differ only in whether
    the interaction has already been answered."""
    accounts = make_accounts(None)
    interaction = make_interaction(response_done=response_done)
    _stub_prompt(monkeypatch, agree=True)

    assert await ensure_consented_account(await gate(interaction), accounts) == 7


async def test_the_gate_stays_silent_when_the_user_was_never_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused prompt already explained why it could not open; do not report a decline."""
    accounts = make_accounts(None)
    ctx = make_context()

    async def prompt(*_args: object, **_kwargs: object) -> object:
        return NOT_ASKED

    monkeypatch.setattr("squid.bot.consent.prompt_for_consent", prompt)

    assert await ensure_consented_account(await gate(ctx), accounts) is None
    assert accounts.identity_creations == []
    ctx.send.assert_not_awaited()


class _Blank(sl.Component[sl.ComponentsV2Target]):
    @override
    def render(self):
        return [sl.primitives.Text("parent")]


async def _collect(into: list[Any], awaitable: Any) -> None:
    into.append(await awaitable)


async def test_a_second_prompt_is_refused_while_the_first_is_open() -> None:
    """Refusing a duplicate preserves the first prompt and its awaiting caller."""
    ctx = make_context()
    request = await gate(ctx)
    key = SessionKey.user("consent", USER_ID)
    outcomes: list[Any] = []

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(lambda: _collect(outcomes, prompt_for_consent(request, user_id=USER_ID, timeout=7)))
        while not ctx.bot.sessions.get(key):
            await anyio.sleep(0)
        first = ctx.bot.sessions.get(key)
        assert next(iter(first[0].message_roots)).timeout == 7

        second = await prompt_for_consent(request, user_id=USER_ID)

        assert second is NOT_ASKED
        assert ctx.bot.sessions.get(key) == first  # the one being awaited is the one that stands
        await ctx.bot.sessions.close(key, disable=False)

    assert outcomes == [None]


async def test_a_closing_parent_ends_the_wait_instead_of_stranding_it() -> None:
    """Closing the parent must also resolve the waiter.

    Disabling buttons alone would leave the awaiting caller blocked until timeout.
    """
    ctx = make_context()
    request = await gate(ctx)
    parent = sd.MessageRoot(_Blank(), access=Everyone(), timeout=None)
    parent_opened = await ctx.bot.sessions.open(parent, delivered_to(message_harness()))
    assert isinstance(parent_opened, Opened)
    outcomes: list[Any] = []

    # Bounded well under the prompt's own 120 s: a wait that only ends when the timeout
    # expires would otherwise pass this test for exactly the wrong reason.
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lambda: _collect(outcomes, prompt_for_consent(request, user_id=USER_ID, parent=parent)))
            while len(parent_opened.session.message_roots) == 1:
                await anyio.sleep(0)

            await parent.finish(disable=False)

    assert outcomes == [None]


def _stub_prompt(monkeypatch: pytest.MonkeyPatch, *, agree: bool) -> None:
    """Replace the waiting prompt with its outcome; the view itself is covered elsewhere."""

    async def prompt(*_args: object, **_kwargs: object) -> AccountConsent | None:
        return AccountConsent.grant_current() if agree else None

    monkeypatch.setattr("squid.bot.consent.prompt_for_consent", prompt)


class _Gate(sd.Screen):
    """A panel that asks for consent and keeps answering presses while the notice is up."""

    policy = sd.ResponseSpec(session=sd.SessionSpec("gate"), timeout=None)

    presses: int = sl.state(default=0)
    granted: bool = sl.state(default=False)
    abandoned: int = sl.state(default=0)

    @override
    def render(self) -> Any:
        return [
            sl.primitives.Text(f"{self.presses}"),
            sl.primitives.Row(
                (
                    sl.primitives.Button("ask", self._ask, "ask"),
                    sl.primitives.Button("count", self._count, "count"),
                )
            ),
        ]

    async def _ask(self, event: sd.PressEvent) -> None:
        opened = await request_consent(
            event,
            user_id=USER_ID,
            on_answer=self._answered,
            on_abandon=self._abandoned,
        )
        if not opened:
            await self._abandoned()

    async def _count(self, event: sl.PressEvent) -> None:
        del event
        self.presses += 1

    async def _answered(self, event: sd.ActionEvent, consent: AccountConsent | None) -> None:
        self.granted = consent is not None

    async def _abandoned(self) -> None:
        self.abandoned += 1


def _clicked(client: Any, *, message_id: int) -> Any:
    """An interaction whose client carries the host the prompt opens through."""
    interaction = interaction_harness(USER_ID, message_id=message_id)
    interaction.client = client
    return interaction


def _prompt_of(panel: _Gate) -> sd.MessageRoot | None:
    """A consent child has its own admission key and the parent's owned lifetime."""
    assert panel.opening is not None
    sessions = panel.opening.runtime.sessions.get(SessionKey.user("consent", USER_ID))
    return next(iter(sessions[0].message_roots), None) if sessions else None


async def test_asking_for_consent_from_a_handler_leaves_the_panel_free() -> None:
    panel = _Gate()
    async with sd.testing.stage(panel, owner=object(), user_id=USER_ID) as ui:
        with anyio.fail_after(5):
            await ui.press("ask")
            assert _prompt_of(panel) is not None
            await ui.press("count")
        assert panel.presses == 1


async def test_the_prompt_carries_the_answer_back_with_fresh_parent_authority() -> None:
    panel = _Gate()
    async with sd.testing.stage(panel, owner=object(), user_id=USER_ID) as ui:
        await ui.press("ask")
        prompt = _prompt_of(panel)
        assert prompt is not None
        commit_render(prompt)
        await prompt.dispatch("accept", _clicked(ui.interaction.client, message_id=3))

        assert panel.granted
        assert panel.abandoned == 0
        assert prompt.finished
        assert not ui.root.finished


@pytest.mark.parametrize("cause", ["closure", "cancel", "timeout", "parent-close"])
async def test_an_uncompleted_prompt_runs_cleanup_once(cause: str) -> None:
    panel = _Gate()
    async with sd.testing.stage(panel, owner=object(), user_id=USER_ID) as ui:
        await ui.press("ask")
        prompt = _prompt_of(panel)
        assert prompt is not None
        if cause == "cancel":
            await prompt.dispatch("cancel", _clicked(ui.interaction.client, message_id=3))
        elif cause == "timeout":
            await prompt.handle_timeout()
        elif cause == "parent-close":
            await ui.root.finish()
        else:
            await prompt.finish()
        await prompt.finish()

        assert panel.abandoned == 1
        assert not panel.granted


async def test_rejected_prompt_leaves_cleanup_to_the_caller_once() -> None:
    panel = _Gate()
    async with sd.testing.stage(panel, owner=object(), user_id=USER_ID) as ui:
        await ui.press("ask")
        prompt = _prompt_of(panel)
        assert prompt is not None
        await ui.press("ask")

        assert panel.abandoned == 1
        assert _prompt_of(panel) is prompt
        assert not prompt.finished
        await prompt.finish()
        assert panel.abandoned == 2


async def test_failed_parent_work_does_not_publish_consent_and_can_retry() -> None:
    class FailingGate(_Gate):
        fail: bool = sl.state(default=True)

        @override
        async def _answered(self, event: sd.ActionEvent, consent: AccountConsent | None) -> None:
            assert event.context.root is not None
            assert event.context.root.component is self
            self.granted = True
            if self.fail:
                raise RuntimeError("business write failed")

    panel = FailingGate()
    async with sd.testing.stage(panel, owner=object(), user_id=USER_ID) as ui:
        await ui.press("ask")
        prompt = _prompt_of(panel)
        assert prompt is not None
        await prompt.dispatch("accept", _clicked(ui.interaction.client, message_id=3))

        assert not panel.granted
        assert not prompt.finished
        assert isinstance(prompt.component, ConsentPrompt)
        assert prompt.component.outcome is None
        assert prompt.component.consent is None
        panel.fail = False
        await prompt.dispatch("accept", _clicked(ui.interaction.client, message_id=4))

        assert panel.granted
        assert prompt.finished
        assert isinstance(await prompt.component.wait(), sd.Completed)


def test_consent_prompt_preserves_large_discord_mentions() -> None:
    user_id = 123456789012345678
    prompt = _consent_prompt(user_id=user_id, preview=None)
    root = sd.MessageRoot(prompt, access=sd.Owner(user_id), timeout=120)
    content = str(commit_render(root).to_components())
    assert f"<@{user_id}>" in content
    assert f"`{user_id}`" in content

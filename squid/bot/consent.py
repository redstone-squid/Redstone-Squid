"""The privacy-notice consent prompt, in an awaiting form for commands and a continuation form for action handlers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import anyio

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    PRIVACY_NOTICE,
    AccountConsent,
    IdentityProvider,
    LinkPreview,
)
from squid.bot.ui import CardField, text_node, tr
from squid.bot.utils.sentinel import Sentinel
from squid_ui_discord.sessions import AdmissionSpec, Reject


class NotAskedType(Enum):
    NOT_ASKED = Sentinel("NOT_ASKED")


NOT_ASKED = NotAskedType.NOT_ASKED
"""The prompt was not opened (admission rejected it); the user has already been told why."""

type ConsentContinuation = Callable[[sl.PressEvent, AccountConsent | None], Awaitable[None]]
"""Runs from the prompt's own press once answered; `None` consent means cancelled.

An action handler must not await the answer: it runs inside its mount's transaction and, under
the default `EXCLUSIVE` policy, its dispatch lock. The continuation runs in the prompt's mount
instead, after the opening press has finished.
"""


@dataclass(slots=True)
class _Answer:
    """The answer, held off the component so the write is not staged by the handler's transaction.

    A declared state cell would be invisible to `wait()` in another task and dropped by the
    `finish` that follows; an undeclared attribute write raises in `Component.__setattr__`.
    """

    consent: AccountConsent | None = None


class ConsentPrompt(sd.Screen):
    """Privacy notice with agree/cancel; one open prompt per user, gone after 120 seconds or on answer.

    A second prompt for the same user is rejected with a notice. `wait()` returns None on cancel,
    on timeout and on unmount; `on_answer` runs only for an explicit answer.
    """

    session = sd.SessionSpec(
        "consent",
        admission=AdmissionSpec(
            collision=Reject(notice=tr(t"You already have a consent prompt open. Please answer that one."))
        ),
    )
    timeout = 120

    closed: bool = sl.state(default=False)

    def __init__(
        self,
        *,
        user_id: int,
        title: sl.TextLike,
        summary: sl.TextLike,
        fields: tuple[CardField, ...],
        accept_label: sl.TextLike,
        wait_timeout: float,
        on_answer: ConsentContinuation | None = None,
    ) -> None:
        self.user_id = user_id
        self._title = title
        self._summary = summary
        self._fields = fields
        self._accept_label = accept_label
        self._wait_timeout = wait_timeout
        self._on_answer = on_answer
        self._answer = _Answer()
        self._done = anyio.Event()

    @property
    def consent(self) -> AccountConsent | None:
        return self._answer.consent

    @property
    def notice_version(self) -> str:
        return CURRENT_CONSENT_VERSION

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        card_fields = tuple(sl.field(field.name, field.value) for field in self._fields)
        return (
            sl.section(
                sl.heading(self._title),
                sl.truncate(sl.paragraph(self._summary)),
                bool(card_fields) and sl.fields(*card_fields),
            ),
            sl.action_controls(
                sl.action_control(
                    self._accept_label,
                    self._accept,
                    key="accept",
                    tone=sl.Tone.SUCCESS,
                ),
                sl.action_control(tr(t"Cancel"), self._cancel, key="cancel"),
                sl.action_control(tr(t"Privacy notice"), self._privacy, key="privacy"),
                key="consent-actions",
            ),
        )

    async def _accept(self, event: sl.PressEvent) -> None:
        await self._finish(event, AccountConsent.grant_current())

    async def _cancel(self, event: sl.PressEvent) -> None:
        await self._finish(event, None)

    async def _privacy(self, event: sl.PressEvent) -> None:
        await event.notice(tr(PRIVACY_NOTICE))

    async def _finish(self, event: sl.PressEvent, consent: AccountConsent | None) -> None:
        self._answer.consent = consent
        self.closed = True
        self._done.set()
        # Finish first: the click is answered within its deadline and nothing after the
        # continuation can roll back what it wrote.
        await event.finish()
        if self._on_answer is not None:
            await self._on_answer(event, consent)

    def on_unmount(self) -> None:
        self._done.set()

    async def wait(self) -> AccountConsent | None:
        with anyio.move_on_after(self._wait_timeout) as scope:
            await self._done.wait()
        return None if scope.cancel_called else self._answer.consent


def _link_credit_value(preview: LinkPreview) -> sl.TextLike:
    credit = preview.credit
    if credit is None:
        username = preview.username
        return tr(t"No build credits **{username}** yet, so nothing is reattributed.")
    count = credit.build_count
    builds = tr(t"{count} build", plural=t"{count} builds")
    if credit.is_contested:
        name = credit.name
        return tr(
            t"**{name}** ({builds}) is already credited to another creator, so agreeing moves nothing and opens a claim for staff to review."
        )
    name = credit.name
    return tr(t"**{name}** ({builds}) becomes attributed to your account.")


async def _show_prompt(
    request: sd.Request[Any],
    *,
    user_id: int,
    preview: LinkPreview | None,
    timeout: float,
    on_answer: ConsentContinuation | None,
    parent: sd.MessageRoot | None,
) -> ConsentPrompt | None:
    """Open the prompt worded for what agreeing stores; None when admission rejected it."""
    version = CURRENT_CONSENT_VERSION
    if preview is None:
        prompt = ConsentPrompt(
            user_id=user_id,
            title=tr(t"Before Redstone Squid stores anything about you"),
            summary=tr(
                t"Agreeing stores your Discord user ID and records this consent, so the bot can "
                t"recognise you and attribute your builds. Cancelling stores nothing."
            ),
            fields=(
                CardField(
                    tr(t"Discord account"),
                    tr(t"<@{user_id}> (`{user_id}`)"),
                ),
                CardField(
                    tr(t"Consent recorded"),
                    tr(t"Notice {version}, timed at the moment you agree."),
                ),
            ),
            accept_label=tr(t"Agree"),
            wait_timeout=timeout,
            on_answer=on_answer,
        )
    else:
        username = preview.username
        uuid = preview.java_uuid
        prompt = ConsentPrompt(
            user_id=user_id,
            title=tr(t"Link {username} to your Discord account"),
            summary=tr(
                t"Agreeing stores your Discord user ID, your Minecraft UUID and your current "
                t"Minecraft username, and records this consent. Cancelling stores nothing."
            ),
            fields=(
                CardField(
                    tr(t"Minecraft account"),
                    tr(t"**{username}**\n`{uuid}`"),
                ),
                CardField(
                    tr(t"Discord account"),
                    tr(t"<@{user_id}> (`{user_id}`)"),
                ),
                CardField(tr(t"Build credit"), _link_credit_value(preview)),
                CardField(
                    tr(t"Consent recorded"),
                    tr(t"Notice {version}, timed at the moment you agree."),
                ),
            ),
            accept_label=tr(t"Agree and link"),
            wait_timeout=timeout,
            on_answer=on_answer,
        )
    outcome = await request.respond(prompt, parent=parent)
    return prompt if isinstance(outcome, sd.Presented) else None


async def prompt_for_consent(
    request: sd.Request[Any],
    *,
    user_id: int,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
    parent: sd.MessageRoot | None = None,
) -> AccountConsent | NotAskedType | None:
    """Show the notice and await the answer; `NOT_ASKED` if the prompt could not open, None on cancel or timeout.

    For commands only. An action handler must use `request_consent`: awaiting here would hold
    the mount's transaction and dispatch lock while the reader reads.
    """
    component = await _show_prompt(
        request,
        user_id=user_id,
        preview=preview,
        timeout=timeout,
        on_answer=None,
        parent=parent,
    )
    if component is None:
        return NOT_ASKED
    return await component.wait()


async def request_consent(
    request: sd.Request[Any],
    *,
    user_id: int,
    on_answer: ConsentContinuation,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
    parent: sd.MessageRoot | None = None,
) -> bool:
    """Show the notice and return at once; `on_answer` runs from the prompt's own press.

    False means the prompt was not opened, the reader has been told why, and `on_answer` never
    runs. An unanswered prompt expires with its mount and never runs it either.
    """
    component = await _show_prompt(
        request,
        user_id=user_id,
        preview=preview,
        timeout=timeout,
        on_answer=on_answer,
        parent=parent,
    )
    return component is not None


type ConsentedAccountWork = Callable[[sl.ActionEvent, int], Awaitable[None]]
"""Runs with whichever press is live: the caller's if consent was current, the prompt's if it had to be asked."""


async def with_consented_account(
    event: sl.ActionEvent,
    accounts: AccountService,
    work: ConsentedAccountWork,
    *,
    timeout: float = 120.0,
) -> None:
    """Run `work` with the reader's consented account id, prompting first when consent is missing or stale.

    The action-handler counterpart of `ensure_consented_account`: never awaits the answer. On the
    prompting path the account is created on agreement and the panel is redrawn through its own
    root, since the prompt's interaction addresses the prompt's message.
    """
    request = await sd.request(event)
    user = request.user
    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        await work(event, account.id)
        return
    message_root = request.root
    assert message_root is not None, "a press always arrives from a mounted message"

    async def answered(prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
        if consent is None:
            return
        granted = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(user.id), consent=consent)
        assert granted.id is not None, "get_or_create_identity always returns a persisted account"
        await work(prompt, granted.id)
        await message_root.schedule()

    await request_consent(
        request,
        user_id=user.id,
        on_answer=answered,
        timeout=timeout,
        parent=message_root,
    )


async def ensure_consented_account(
    request: sd.Request[Any],
    accounts: AccountService,
    *,
    timeout: float = 120.0,
    parent: sd.MessageRoot | None = None,
) -> int | None:
    """The user's account id, prompting for consent first when missing or stale; None if refused or not asked.

    Awaits the answer, so for commands only; action handlers use `with_consented_account`. A
    refusal is answered with a personal "Cancelled" notice; `NOT_ASKED` is answered by admission.
    """
    user = request.user
    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        return account.id

    consent = await prompt_for_consent(request, user_id=user.id, timeout=timeout, parent=parent)
    if consent is NOT_ASKED or consent is None:
        if consent is None:
            await request.respond(text_node(tr(t"Cancelled. No account information was stored.")), audience="personal")
        return None

    granted = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(user.id), consent=consent)
    assert granted.id is not None, "get_or_create_identity always returns a persisted account"
    return granted.id

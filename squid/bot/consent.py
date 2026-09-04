"""Asking one Discord user for informed consent, and continuing what they asked for."""

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
"""The question was never put to the user, and they have already been told why."""

type ConsentContinuation = Callable[[sl.PressEvent, AccountConsent | None], Awaitable[None]]
"""What to do once the reader has answered, run by the prompt's own press.

The awaiting form is only safe where the caller owns the wait. Inside a mounted action
handler it is not: the handler runs in the mount's transaction and, under the default
`EXCLUSIVE` policy, in its dispatch lock, so awaiting an answer holds both for as long as
the reader takes to read. A continuation runs in the *prompt's* dispatch instead, which is
a separate mount, so the press that opened it is already finished by then.
"""

CONSENT_PROMPT_TIMEOUT_SECONDS = 120.0


@dataclass(slots=True)
class _Answer:
    """What the reader said, held off the component on purpose.

    Nothing renders it, and it has to outlive the press that writes it: the answering handler
    runs in a transaction, so a declared cell would only be staged -- invisible to a waiter in
    another task, and dropped by the teardown `finish` performs a line later. Writing through
    an object the component merely holds is what keeps it off `Component.__setattr__`, which
    is where an undeclared write raises.
    """

    consent: AccountConsent | None = None


class ConsentPrompt(sd.Screen):
    """A semantic consent prompt with a native-free waiting lifecycle."""

    session = sd.SessionSpec(
        "consent",
        admission=AdmissionSpec(
            collision=Reject(notice=tr(t"You already have a consent prompt open. Please answer that one."))
        ),
    )
    timeout = CONSENT_PROMPT_TIMEOUT_SECONDS

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
        # The continuation runs last, so nothing in this handler can fail after it and roll
        # back what it wrote. Closing first also answers the click inside its own deadline,
        # whatever the continuation then goes on to do.
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
    on_abandon: Callable[[], Awaitable[None]] | None,
    parent: sd.MessageRoot | None,
) -> ConsentPrompt | None:
    """The notice this reader is owed, worded for what agreeing would actually store."""
    prompt = _consent_prompt(
        user_id=user_id,
        preview=preview,
        timeout=timeout,
        on_answer=on_answer,
    )
    outcome = await request.respond(prompt, parent=parent)
    if not isinstance(outcome, sd.Presented):
        return None
    if on_abandon is not None:

        async def abandon_unanswered(_root: sd.MessageRoot) -> None:
            if not prompt.closed:
                await on_abandon()

        if outcome.root.finished:
            await abandon_unanswered(outcome.root)
        else:
            outcome.root.on_finish(abandon_unanswered)
    return prompt


def _consent_prompt(
    *,
    user_id: int,
    preview: LinkPreview | None,
    timeout: float,
    on_answer: ConsentContinuation | None,
) -> ConsentPrompt:
    """Build the exact notice shown before an account or identity is stored."""
    version = CURRENT_CONSENT_VERSION
    if preview is None:
        return ConsentPrompt(
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
    username = preview.username
    uuid = preview.java_uuid
    return ConsentPrompt(
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


async def prompt_for_consent(
    request: sd.Request[Any],
    *,
    user_id: int,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
    parent: sd.MessageRoot | None = None,
) -> AccountConsent | NotAskedType | None:
    """Show the notice and wait, returning the consent the user granted.

    For callers that own their own wait -- a command, which holds no mount state while it
    blocks. A mounted action handler wants `request_consent` instead, because the wait it
    would do here happens inside the mount's transaction and dispatch lock.
    """
    component = await _show_prompt(
        request,
        user_id=user_id,
        preview=preview,
        timeout=timeout,
        on_answer=None,
        on_abandon=None,
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
    on_abandon: Callable[[], Awaitable[None]] | None = None,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
    parent: sd.MessageRoot | None = None,
) -> bool:
    """Show the notice and return, running `on_answer` from the prompt's own press.

    Returns whether the prompt was opened; `False` means the reader has already been told
    why not, and `on_answer` will never run. An unanswered prompt expires with its mount and
    also never runs it. When supplied, `on_abandon` runs from the prompt root's owned finish
    lifecycle so callers can release expiring authority without starting a detached task.
    """
    component = await _show_prompt(
        request,
        user_id=user_id,
        preview=preview,
        timeout=timeout,
        on_answer=on_answer,
        on_abandon=on_abandon,
        parent=parent,
    )
    return component is not None


type ConsentedAccountWork = Callable[[sl.ActionEvent, int], Awaitable[None]]
"""Work needing a consented account id, run against whichever press is live when it runs."""


async def with_consented_account(
    event: sl.ActionEvent,
    accounts: AccountService,
    work: ConsentedAccountWork,
    *,
    timeout: float = 120.0,
) -> None:
    """Run `work` with the reader's consented account id, asking first when consent is stale.

    The mounted counterpart of `ensure_consented_account`, which may not be called from an
    action handler: this one never awaits the answer, so the press that called it ends and
    the panel's transaction and dispatch lock end with it.

    `work` receives whichever press is live when it runs -- this one when the reader had
    already agreed, the prompt's own when the question had to be put -- so a notice always
    answers the click the reader last made. On the asking path the panel is redrawn here,
    through its own handle: the prompt's interaction addresses the prompt's message, and
    flushing the panel through it would draw the panel into the dialog.
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
    """Return the user's account id after current consent has been granted.

    Awaits the answer, so it belongs to a command rather than to a mounted action handler;
    `with_consented_account` is the form for the latter.
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

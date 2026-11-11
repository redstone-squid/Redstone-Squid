"""Ask a Discord user for consent before continuing an account operation.

Commands use ensure_consented_account and can wait for the answer. An action on
an already displayed panel uses with_consented_account: it opens a separate
prompt and returns, then continues from the acceptance click. This releases the
panel's UI action transaction and dispatch lock while the user reads the notice.
request_consent provides the same callback flow for operations such as linking
an identity that need their own acceptance and cleanup logic.
"""

from collections.abc import Awaitable, Callable
from typing import Any, override

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
from squid.core.sentinel import NOT_ASKED

type ConsentContinuation = Callable[[sd.ActionEvent, AccountConsent | None], Awaitable[None]]
"""What to do once the reader accepts, run with fresh parent action authority.

The awaiting form is only safe where the caller owns the wait. Inside a mounted action
handler it is not: the handler runs in the mount's UI action transaction and, under the default
`EXCLUSIVE` policy, in its dispatch lock, so awaiting an answer holds both for as long as
the reader takes to read. A continuation runs in a fresh parent dispatch after the child
accepts, so the press that opened it has already finished.
"""

CONSENT_PROMPT_TIMEOUT_SECONDS = 120.0


class ConsentPrompt(sd.Screen[AccountConsent]):
    """A user-owned notice that publishes consent only after the accepting action commits."""

    policy = sd.ResponseSpec(
        session=sd.SessionSpec(
            "consent",
            admission=sd.AdmissionSpec(
                collision=sd.Reject(
                    notice=tr(
                        t"consent-prompt-already-open = You already have a consent prompt open. Please answer that one."
                    )
                )
            ),
        ),
        timeout=CONSENT_PROMPT_TIMEOUT_SECONDS,
    )

    def __init__(
        self,
        *,
        user_id: int,
        title: sl.TextLike,
        summary: sl.TextLike,
        fields: tuple[CardField, ...],
        accept_label: sl.TextLike,
    ) -> None:
        self.user_id = user_id
        self._title = title
        self._summary = summary
        self._fields = fields
        self._accept_label = accept_label

    @property
    def consent(self) -> AccountConsent | None:
        outcome = self.outcome
        return outcome.value if isinstance(outcome, sd.Completed) else None

    @property
    def notice_version(self) -> str:
        return CURRENT_CONSENT_VERSION

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        card_fields = tuple(sl.field(field.name, field.value) for field in self._fields)
        return (
            sl.section(
                sl.heading(self._title),
                sl.paragraph(self._summary).truncate(),
                bool(card_fields) and sl.fields(*card_fields),
            ),
            sl.action_controls(
                sl.action_control(
                    self._accept_label,
                    self._accept,
                    key="accept",
                    tone=sl.Tone.SUCCESS,
                ),
                sl.action_control(tr(t"consent-cancel = Cancel"), self._cancel, key="cancel"),
                sl.action_control(tr(t"consent-privacy-notice = Privacy notice"), self._privacy, key="privacy"),
                key="consent-actions",
            ),
        )

    async def _accept(self, event: sd.PressEvent) -> None:
        self.complete(event, AccountConsent.grant_current())

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)

    async def _privacy(self, event: sd.PressEvent) -> None:
        await event.notice(PRIVACY_NOTICE)


def _link_credit_value(preview: LinkPreview) -> sl.TextLike:
    credit = preview.credit
    if credit is None:
        username = preview.username
        return tr(t"consent-no-credits-notice = No build credits **{username}** yet, so nothing is reattributed.")
    count = credit.build_count
    builds = tr(t"""
        bot-consent-build = {{ $count ->
            [one] {count} build
           *[other] {count} builds
        }}
        """)
    if credit.is_contested:
        name = credit.name
        return tr(
            t"consent-existing-credit-warning = **{name}** ({builds}) is already credited to "
            t"another creator, so agreeing moves nothing and opens a claim for staff to review."
        )
    name = credit.name
    return tr(t"consent-credit-attribution = **{name}** ({builds}) becomes attributed to your account.")


def _consent_prompt(
    *,
    user_id: int,
    preview: LinkPreview | None,
) -> ConsentPrompt:
    """Build the exact notice shown before an account or identity is stored."""
    version = CURRENT_CONSENT_VERSION
    if preview is None:
        return ConsentPrompt(
            user_id=user_id,
            title=tr(t"consent-storage-prerequisite = Before Redstone Squid stores anything about you"),
            summary=tr(
                t"consent-data-storage-summary = Agreeing stores your Discord user "
                t"ID and records this consent, so the bot can recognise you and attribute your builds. "
                t"Cancelling stores nothing."
            ),
            fields=(
                CardField(
                    tr(t"consent-discord-account = Discord account"),
                    tr(t"consent-value-2 = <@{(user_id_text := str(user_id))}> (`{(user_id_text := str(user_id))}`)"),
                ),
                CardField(
                    tr(t"consent-consent-recorded = Consent recorded"),
                    tr(t"consent-notice-versioning = Notice {version}, timed at the moment you agree."),
                ),
            ),
            accept_label=tr(t"consent-agree = Agree"),
        )
    username = preview.username
    uuid = preview.java_uuid
    return ConsentPrompt(
        user_id=user_id,
        title=tr(t"consent-link-to-your-discord-account = Link {username} to your Discord account"),
        summary=tr(
            t"consent-agreement-details = Agreeing stores your Discord user "
            t"ID, your Minecraft UUID and your current Minecraft username, and records this consent. "
            t"Cancelling stores nothing."
        ),
        fields=(
            CardField(
                tr(t"consent-minecraft-account = Minecraft account"),
                tr(t"""
                    bot-consent-value =
                        {{ "*" }}*{username}**
                        `{uuid}`
                    """),
            ),
            CardField(
                tr(t"consent-discord-account = Discord account"),
                tr(t"consent-value-2 = <@{(user_id_text := str(user_id))}> (`{(user_id_text := str(user_id))}`)"),  # noqa: F841 - captured text identifier
            ),
            CardField(tr(t"consent-build-credit = Build credit"), _link_credit_value(preview)),
            CardField(
                tr(t"consent-consent-recorded = Consent recorded"),
                tr(t"consent-notice-versioning = Notice {version}, timed at the moment you agree."),
            ),
        ),
        accept_label=tr(t"consent-agree-and-link = Agree and link"),
    )


async def prompt_for_consent(
    request: sd.Request[Any],
    *,
    user_id: int,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
    parent: sd.MessageRoot | None = None,
) -> AccountConsent | NOT_ASKED | None:
    """Present a notice and wait for the user's answer.

    Return consent on acceptance, None on cancellation, unmount, or wait timeout,
    and NOT_ASKED when presentation is rejected. Use `request_consent` inside
    mounted action handlers to avoid holding their transaction and dispatch lock.
    """
    component = _consent_prompt(user_id=user_id, preview=preview)
    presented = await request.respond(component, timeout=timeout, parent=parent)
    if not isinstance(presented, sd.Presented):
        return NOT_ASKED
    outcome = await component.wait()
    return outcome.value if isinstance(outcome, sd.Completed) else None


async def request_consent(
    event: sd.ActionEvent,
    *,
    user_id: int,
    on_answer: ConsentContinuation,
    on_abandon: Callable[[], Awaitable[None]] | None = None,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
) -> bool:
    """Open a child notice and return without holding the parent's action open.

    Acceptance runs on_answer with fresh parent action authority. Cancellation, timeout,
    and closure run on_abandon without an action event. False means presentation failed;
    the caller retains responsibility for releasing any reservation in that case.
    """
    root = event.context.root
    assert root is not None and isinstance(root.component, sd.Screen), "consent actions require a parent Screen"
    prompt = _consent_prompt(user_id=user_id, preview=preview)
    opened = False

    async def answered(result: sd.ScreenResultEvent[AccountConsent]) -> None:
        await on_answer(result, result.value)

    async def abandoned(_outcome: sd.Cancelled) -> None:
        if opened and on_abandon is not None:
            await on_abandon()

    outcome = await root.component.open_child(event, prompt, on_result=answered, on_cancel=abandoned, timeout=timeout)
    opened = isinstance(outcome, sd.Presented)
    return opened


type ConsentedAccountWork = Callable[[sd.ActionEvent, int], Awaitable[None]]
"""Work needing a consented account id, run against whichever press is live when it runs."""


async def with_consented_account(
    event: sd.ActionEvent,
    accounts: AccountService,
    work: ConsentedAccountWork,
    *,
    timeout: float = 120.0,
) -> None:
    """Run work with the current user's consented account ID.

    If consent is missing or stale, open a prompt and return without waiting.
    Acceptance creates or updates the account and runs `work` in a fresh parent
    action; cancellation performs no work. The callback receives the live action
    for notices, and the original panel is scheduled to redraw afterwards.
    """
    request = event.context
    user = request.user
    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        await work(event, account.id)
        return
    message_root = request.root
    assert message_root is not None, "a press always arrives from a mounted message"

    async def answered(prompt: sd.ActionEvent, consent: AccountConsent | None) -> None:
        if consent is None:
            return
        granted = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(user.id), consent=consent)
        assert granted.id is not None, "get_or_create_identity always returns a persisted account"
        await work(prompt, granted.id)
        await message_root.schedule()

    await request_consent(
        event,
        user_id=user.id,
        on_answer=answered,
        timeout=timeout,
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
            await request.respond(
                text_node(tr(t"consent-cancelled-no-data = Cancelled. No account information was stored.")),
                audience="personal",
            )
        return None

    granted = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(user.id), consent=consent)
    assert granted.id is not None, "get_or_create_identity always returns a persisted account"
    return granted.id

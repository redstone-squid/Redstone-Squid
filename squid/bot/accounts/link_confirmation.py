"""Confirm a Minecraft identity link and resolve Discord accounts for user actions."""

from collections.abc import Awaitable, Callable
from typing import override

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import CardField, tr
from squid.core.accounts.domain import (
    PRIVACY_NOTICE,
    PRIVACY_NOTICE_VERSION,
    LinkPreview,
    PrivacyNoticeReceipt,
)
from squid_i18n import md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target

type LinkContinuation = Callable[[sd.ActionEvent, PrivacyNoticeReceipt | None], Awaitable[None]]
"""What to do once the reader accepts, run with fresh parent action authority.

The awaiting form is only safe where the caller owns the wait. Inside a mounted action
handler it is not: the handler runs in the mount's UI action transaction and, under the default
`EXCLUSIVE` policy, in its dispatch lock, so awaiting an answer holds both for as long as
the reader takes to read. A continuation runs in a fresh parent dispatch after the child
accepts, so the press that opened it has already finished.
"""

LINK_CONFIRMATION_TIMEOUT_SECONDS = 120.0


class LinkConfirmation(sd.Screen[PrivacyNoticeReceipt]):
    """A user-owned notice that publishes consent only after the accepting action commits."""

    policy = sd.ResponseSpec(
        session=sd.SessionSpec(
            "link-confirmation",
            admission=sd.AdmissionSpec(
                collision=sd.Reject(
                    notice=tr(t"consent-prompt-already-open = Finish the account confirmation you already have open.")
                )
            ),
        ),
        timeout=LINK_CONFIRMATION_TIMEOUT_SECONDS,
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

    @classmethod
    def from_preview(
        cls,
        *,
        user_id: int,
        preview: LinkPreview,
    ) -> LinkConfirmation:
        """Build the exact notice shown before an account or identity is stored."""
        version = PRIVACY_NOTICE_VERSION
        user_mention = sl.mention(user_id)
        username = preview.username
        uuid = preview.java_uuid
        return cls(
            user_id=user_id,
            title=md_tr(t"consent-link-to-your-discord-account = Link {username} to your Discord account"),
            summary=tr(
                t"consent-agreement-details = Linking stores your Discord user ID, Minecraft UUID and current "
                t"Minecraft username so builds credited to that name can be associated with your account."
            ),
            fields=(
                CardField(
                    tr(t"consent-minecraft-account = Minecraft account"),
                    md_tr(
                        t"""
                        bot-consent-value =
                            {{ "*" }}*{username}**
                            `{uuid}`
                        """,
                    ),
                ),
                CardField(
                    tr(t"consent-discord-account = Discord account"),
                    md_tr(
                        t"consent-value-2 = {user_mention} (`{user_id!s}`)",
                    ),
                ),
                CardField(tr(t"consent-build-credit = Build credit"), _link_credit_value(preview)),
                CardField(
                    tr(t"consent-consent-recorded = Privacy notice version"),
                    md_tr(t"consent-notice-versioning = Notice {version}, timed at the moment you agree."),
                ),
            ),
            accept_label=tr(t"consent-agree-and-link = Link Minecraft account"),
        )

    @property
    def consent(self) -> PrivacyNoticeReceipt | None:
        outcome = self.outcome
        return outcome.value if isinstance(outcome, sd.Completed) else None

    @property
    def notice_version(self) -> str:
        return PRIVACY_NOTICE_VERSION

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
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
        self.complete(event, PrivacyNoticeReceipt.grant_current())

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)

    async def _privacy(self, event: sd.PressEvent) -> None:
        await event.notice(PRIVACY_NOTICE)


def _link_credit_value(preview: LinkPreview) -> sl.TextLike:
    credit = preview.credit
    if credit is None:
        username = preview.username
        return md_tr(
            t"consent-no-credits-notice = No build credits **{username}** yet, so nothing is reattributed.",
        )
    count = credit.build_count
    builds = md_tr(
        t"""
        bot-consent-build = {{ $count ->
            [one] {count} build
           *[other] {count} builds
        }}
        """,
    )
    if credit.is_contested:
        name = credit.name
        return md_tr(
            t"consent-existing-credit-warning = **{name}** ({builds}) is already credited to "
            t"another creator, so agreeing moves nothing and opens a claim for staff to review.",
        )
    name = credit.name
    return md_tr(t"consent-credit-attribution = **{name}** ({builds}) becomes attributed to your account.")


async def request_link_confirmation(
    event: sd.ActionEvent,
    *,
    user_id: int,
    on_answer: LinkContinuation,
    on_abandon: Callable[[], Awaitable[None]] | None = None,
    preview: LinkPreview,
    timeout: float = 120.0,
) -> bool:
    """Open a child notice and return without holding the parent's action open.

    Acceptance runs on_answer with fresh parent action authority. Cancellation, timeout,
    and closure run on_abandon without an action event. False means presentation failed;
    the caller retains responsibility for releasing any reservation in that case.
    """
    root = event.context.root
    assert root is not None and isinstance(root.component, sd.Screen), "consent actions require a parent Screen"
    prompt = LinkConfirmation.from_preview(user_id=user_id, preview=preview)
    opened = False

    async def answered(result: sd.ScreenResultEvent[PrivacyNoticeReceipt]) -> None:
        await on_answer(result, result.value)

    async def abandoned(_outcome: sd.Cancelled) -> None:
        if opened and on_abandon is not None:
            await on_abandon()

    outcome = await root.component.open_child(event, prompt, on_result=answered, on_cancel=abandoned, timeout=timeout)
    opened = isinstance(outcome, sd.Presented)
    return opened

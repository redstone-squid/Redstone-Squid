"""Asking one Discord user for informed consent, and continuing what they asked for."""

from enum import Enum
from typing import Any, cast, override

import anyio
import discord
from discord.ext import commands

import squid_layouts as sl
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    PRIVACY_NOTICE,
    AccountConsent,
    IdentityProvider,
    LinkPreview,
)
from squid.bot.errors import ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.ui import localization_for
from squid.bot.utils.components import CardField, card_container, edit_interaction_layout, no_mentions, text_layout
from squid.bot.utils.sentinel import Sentinel
from squid.core.i18n import _, ntranslate
from squid_layouts.discord import Screen, SessionRegistry
from squid_layouts.discord.screens import Opener
from squid_layouts.discord.sessions import Opened, Reject, Rejected, SessionPolicy

CONSENT_SCREEN = Screen(
    "consent",
    policy=SessionPolicy(collision=Reject()),
    options={"timeout": 120},
)


class NotAskedType(Enum):
    NOT_ASKED = Sentinel("NOT_ASKED")


NOT_ASKED = NotAskedType.NOT_ASKED
"""The question was never put to the user, and they have already been told why.

Distinct from `None`, which means they were asked and did not agree. A caller that reports
`None` as "cancelled, nothing was stored" would be misreporting this: nothing was cancelled,
because nothing was asked. Both mean "stop", but only one of them is news.
"""

type ConsentTarget = commands.Context[Any] | discord.Interaction[Any]
"""Anywhere the bot can both identify a user and answer them.

Prefix commands, hybrid commands, slash-only cogs, modals and view buttons all reach the gate,
and the difference between them is only how a message gets sent.
"""


class ConsentPrompt(sl.Component):
    """A semantic consent prompt with a native-free waiting lifecycle."""

    closed: bool = sl.state(default=False)

    def __init__(
        self,
        *,
        user_id: int,
        title: str,
        summary: str,
        fields: tuple[CardField, ...],
        accept_label: str,
        locale: str | None,
        timeout: float,
    ) -> None:
        self.user_id = user_id
        self.locale = locale
        self._title = title
        self._summary = summary
        self._fields = fields
        self._accept_label = accept_label
        self._timeout = timeout
        self._consent: AccountConsent | None = None
        self._done = anyio.Event()

    @property
    def consent(self) -> AccountConsent | None:
        return self._consent

    @property
    def notice_version(self) -> str:
        return CURRENT_CONSENT_VERSION

    def render(self) -> tuple[sl.LayoutNode, ...]:
        card_fields = tuple(sl.field(field.name, field.value) for field in self._fields)
        return (
            sl.section(
                sl.heading(self._title),
                # The summary is the card's shock absorber: truncate lets it give up
                # characters under pressure before a field loses any.
                sl.truncate(sl.paragraph(self._summary)),
                bool(card_fields) and sl.fields(*card_fields),
            ),
            sl.primitives.Row(
                (
                    sl.primitives.Button(
                        self._accept_label,
                        self._accept,
                        "accept",
                        style=sl.primitives.ActionStyle.SUCCESS,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Cancel")),
                        self._cancel,
                        "cancel",
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Privacy notice")),
                        self._privacy,
                        "privacy",
                    ),
                )
            ),
        )

    async def _accept(self, event: sl.PressEvent) -> None:
        self._consent = AccountConsent.grant_current()
        await self._finish(event)

    async def _cancel(self, event: sl.PressEvent) -> None:
        await self._finish(event)

    async def _privacy(self, event: sl.PressEvent) -> None:
        await event.notice(t(self.locale, PRIVACY_NOTICE))

    async def _finish(self, event: sl.PressEvent) -> None:
        self.closed = True
        self._done.set()
        await event.finish()

    def on_unmount(self) -> None:
        """Stop waiting when the prompt leaves its mount for any reason.

        Deliberately do not touch ``closed`` here: unmounting runs outside an action
        transaction, while waking the local waiter is ordinary lifecycle cleanup.
        """
        self._done.set()

    async def wait(self) -> AccountConsent | None:
        with anyio.move_on_after(self._timeout) as scope:
            await self._done.wait()
        return None if scope.cancel_called else self._consent


class ConsentPromptView(ExpiringLayoutView):
    """Ask one Discord user to accept the current privacy notice.

    The card names the stored categories itself and keeps the full notice behind a button:
    consent is not informed if every category is behind a button, and it is not read if the whole
    policy is in front of one.
    """

    actions = discord.ui.ActionRow()

    def __init__(self, user_id: int, *, locale: str | None = None, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.locale = locale
        self.consent: AccountConsent | None = None
        controls = self.actions
        self.clear_items()
        self.add_item(card_container(self._title(locale), self._summary(locale), fields=self._fields(locale)))
        self.add_item(controls)
        self.accept.label = self._accept_label(locale)
        self.cancel.label = t(locale, _("Cancel"))
        self.privacy.label = t(locale, _("Privacy notice"))

    def _title(self, locale: str | None) -> str:
        return t(locale, _("Before Redstone Squid stores anything about you"))

    def _summary(self, locale: str | None) -> str:
        return t(
            locale,
            _(
                "Agreeing stores your Discord user ID and records this consent, so the bot can "
                "recognise you and attribute your builds. Cancelling stores nothing."
            ),
        )

    def _accept_label(self, locale: str | None) -> str:
        return t(locale, _("Agree"))

    def _fields(self, locale: str | None) -> tuple[CardField, ...]:
        """Lay out exactly what agreeing will write."""
        return (
            CardField(
                t(locale, _("Discord account")),
                t(locale, _("<@{user_id}> (`{user_id}`)"), user_id=self.user_id),
            ),
            CardField(
                t(locale, _("Consent recorded")),
                t(locale, _("Notice `{version}`, timed at the moment you agree."), version=CURRENT_CONSENT_VERSION),
            ),
        )

    @override
    async def interaction_check(self, interaction: discord.Interaction[Any], /) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("Only the person this prompt is for can answer it.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @actions.button(label="Agree", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        self.consent = AccountConsent.grant_current()
        await self._finish(interaction)

    @actions.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        await self._finish(interaction)

    @actions.button(label="Privacy notice", style=discord.ButtonStyle.secondary)
    async def privacy(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        """Show the full notice without answering the prompt either way."""
        await interaction.response.send_message(
            t(self.locale, PRIVACY_NOTICE),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )

    async def _finish(self, interaction: discord.Interaction[Any]) -> None:
        """Render the prompt inert before releasing the waiting command."""
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        await edit_interaction_layout(interaction, self)
        self.stop()

    @property
    def notice_version(self) -> str:
        """Return the privacy notice version presented by this view."""
        return CURRENT_CONSENT_VERSION


class LinkConsentView(ConsentPromptView):
    """The account-link prompt, built around a *preview* rather than prose.

    The prompt used to describe categories of data because it ran before the code was redeemed and
    could not know anything concrete; a held code means it can name the Minecraft account, the
    credit at stake and the receipt it will write, which is what makes the decision an informed one
    rather than a policy to skim.
    """

    def __init__(
        self,
        user_id: int,
        preview: LinkPreview,
        *,
        locale: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        # Set before `super().__init__`, which calls the hooks below.
        self.preview = preview
        super().__init__(user_id, locale=locale, timeout=timeout)

    @override
    def _title(self, locale: str | None) -> str:
        return t(locale, _("Link {username} to your Discord account"), username=self.preview.username)

    @override
    def _summary(self, locale: str | None) -> str:
        return t(
            locale,
            _(
                "Agreeing stores your Discord user ID, your Minecraft UUID and your current "
                "Minecraft username, and records this consent. Cancelling stores nothing."
            ),
        )

    @override
    def _accept_label(self, locale: str | None) -> str:
        return t(locale, _("Agree and link"))

    @override
    def _fields(self, locale: str | None) -> tuple[CardField, ...]:
        """Lay out exactly what redeeming this code will write."""
        return (
            CardField(
                t(locale, _("Minecraft account")),
                t(
                    locale,
                    _("**{username}**\n`{uuid}`"),
                    username=self.preview.username,
                    uuid=self.preview.java_uuid,
                ),
            ),
            CardField(
                t(locale, _("Discord account")),
                t(locale, _("<@{user_id}> (`{user_id}`)"), user_id=self.user_id),
            ),
            CardField(t(locale, _("Build credit")), self._credit_value(locale)),
            CardField(
                t(locale, _("Consent recorded")),
                t(locale, _("Notice `{version}`, timed at the moment you agree."), version=CURRENT_CONSENT_VERSION),
            ),
        )

    def _credit_value(self, locale: str | None) -> str:
        """Say what happens to the creator credit, including when nothing happens."""
        credit = self.preview.credit
        if credit is None:
            return t(
                locale,
                _("No build credits **{username}** yet, so nothing is reattributed."),
                username=self.preview.username,
            )

        builds = ntranslate(
            locale,
            _("{count} build"),
            _("{count} builds"),
            credit.build_count,
            count=credit.build_count,
        )
        if credit.is_contested:
            # Naming the outcome up front, because this is the case where agreeing does *not* do the
            # thing the rest of the card implies.
            return t(
                locale,
                _(
                    "**{name}** ({builds}) is already credited to another creator, so agreeing moves "
                    "nothing and opens a claim for staff to review."
                ),
                name=credit.name,
                builds=builds,
            )
        return t(
            locale,
            _("**{name}** ({builds}) becomes attributed to your account."),
            name=credit.name,
            builds=builds,
        )


def _is_context(target: ConsentTarget) -> bool:
    """Whether this is a command context rather than a bare interaction.

    Duck-typed rather than `isinstance(target, commands.Context)`, matching `resolve_locale`, so
    the lightweight doubles in `tests/helpers/discord.py` work without subclassing discord types.
    """
    return hasattr(target, "author")


def _destination(target: ConsentTarget) -> sl.discord.Destination:
    """Where a consent prompt goes, through whichever surface the caller arrived on.

    Both surfaces hand a message back: the prompt is waited on, so it needs credentials to
    edit itself with when the answer lands or the timeout expires.
    """
    ephemeral = _default_ephemeral(target)
    if _is_context(target):
        return sl.discord.reply_to(cast(commands.Context[Any], target), ephemeral=ephemeral)
    return sl.discord.respond_to(cast(discord.Interaction[Any], target), ephemeral=ephemeral, wait=True)


async def _send(target: ConsentTarget, view: discord.ui.LayoutView) -> None:
    """Send one plain layout where the prompt itself would have gone."""
    await _destination(target)(sl.discord.presentation.DiscordPresentation.components_v2(view))


def _default_ephemeral(target: ConsentTarget) -> bool:
    """Keep a consent prompt out of the channel wherever the surface allows it."""
    if not _is_context(target):
        return True
    return cast(commands.Context[Any], target).interaction is not None


def _user_of(target: ConsentTarget) -> discord.User | discord.Member:
    if _is_context(target):
        return cast(commands.Context[Any], target).author
    return cast(discord.Interaction[Any], target).user


def _registry_of(target: ConsentTarget) -> SessionRegistry:
    """The bot's session registry, through whichever surface the caller arrived on."""
    if _is_context(target):
        return cast(Any, cast(commands.Context[Any], target).bot).mounts
    return cast(Any, cast(discord.Interaction[Any], target).client).mounts


def _link_credit_value(preview: LinkPreview, locale: str | None) -> str:
    credit = preview.credit
    if credit is None:
        return t(
            locale,
            _("No build credits **{username}** yet, so nothing is reattributed."),
            username=preview.username,
        )
    builds = ntranslate(
        locale,
        _("{count} build"),
        _("{count} builds"),
        credit.build_count,
        count=credit.build_count,
    )
    if credit.is_contested:
        return t(
            locale,
            _(
                "**{name}** ({builds}) is already credited to another creator, so agreeing moves "
                "nothing and opens a claim for staff to review."
            ),
            name=credit.name,
            builds=builds,
        )
    return t(
        locale,
        _("**{name}** ({builds}) becomes attributed to your account."),
        name=credit.name,
        builds=builds,
    )


async def prompt_for_consent(
    target: ConsentTarget,
    *,
    user_id: int,
    locale: str | None = None,
    preview: LinkPreview | None = None,
    timeout: float = 120.0,
    parent: sl.discord.Mount | None = None,
) -> AccountConsent | NotAskedType | None:
    """Show the notice and wait, returning the receipt the user granted.

    `None` covers both cancelling and letting the prompt expire; neither stores anything, so the
    caller treats them the same and only the wording differs. `NOT_ASKED` is the third outcome:
    the user was never asked, has already been told why, and the caller should stay silent
    rather than report a cancellation that did not happen.

    One prompt per user at a time. A second is refused rather than replacing the first, because
    the first is being awaited somewhere and the two are rarely about the same thing -- replacing
    a `/verify` prompt with an `/account` one would abandon the verification the user started.

    `parent` is the mount this prompt was opened from, when it was opened from one. The prompt
    lives on its own message, so without it a closed parent leaves the prompt clickable until its
    own timer runs out.
    """
    if preview is None:
        component = ConsentPrompt(
            user_id=user_id,
            title=t(locale, _("Before Redstone Squid stores anything about you")),
            summary=t(
                locale,
                _(
                    "Agreeing stores your Discord user ID and records this consent, so the bot can "
                    "recognise you and attribute your builds. Cancelling stores nothing."
                ),
            ),
            fields=(
                CardField(
                    t(locale, _("Discord account")),
                    t(locale, _("<@{user_id}> (\x60{user_id}\x60)"), user_id=user_id),
                ),
                CardField(
                    t(locale, _("Consent recorded")),
                    t(
                        locale,
                        _("Notice {version}, timed at the moment you agree."),
                        version=CURRENT_CONSENT_VERSION,
                    ),
                ),
            ),
            accept_label=t(locale, _("Agree")),
            locale=locale,
            timeout=timeout,
        )
    else:
        component = ConsentPrompt(
            user_id=user_id,
            title=t(locale, _("Link {username} to your Discord account"), username=preview.username),
            summary=t(
                locale,
                _(
                    "Agreeing stores your Discord user ID, your Minecraft UUID and your current "
                    "Minecraft username, and records this consent. Cancelling stores nothing."
                ),
            ),
            fields=(
                CardField(
                    t(locale, _("Minecraft account")),
                    t(
                        locale,
                        _("**{username}**\n\x60{uuid}\x60"),
                        username=preview.username,
                        uuid=preview.java_uuid,
                    ),
                ),
                CardField(
                    t(locale, _("Discord account")),
                    t(locale, _("<@{user_id}> (\x60{user_id}\x60)"), user_id=user_id),
                ),
                CardField(t(locale, _("Build credit")), _link_credit_value(preview, locale)),
                CardField(
                    t(locale, _("Consent recorded")),
                    t(
                        locale,
                        _("Notice {version}, timed at the moment you agree."),
                        version=CURRENT_CONSENT_VERSION,
                    ),
                ),
            ),
            accept_label=t(locale, _("Agree and link")),
            locale=locale,
            timeout=timeout,
        )
    registry = _registry_of(target)
    opened = await CONSENT_SCREEN.open(
        registry,
        component,
        _destination(target),
        opener=Opener(user_id),
        parent=parent,
        localization=localization_for(locale),
        timeout=timeout,
    )
    if isinstance(opened, Rejected):
        await _send(
            target,
            text_layout(t(locale, _("You already have a consent prompt open. Please answer that one."))),
        )
        return NOT_ASKED
    if not isinstance(opened, Opened):
        # An abandoned destination has already explained why it delivered nothing.
        return NOT_ASKED
    return await component.wait()


async def ensure_consented_account(
    target: ConsentTarget,
    accounts: AccountService,
    *,
    locale: str | None = None,
    timeout: float = 120.0,
    parent: sl.discord.Mount | None = None,
) -> int | None:
    """The account id behind this Discord user, once it has accepted the current notice.

    Returns `None` when the prompt was declined or expired. The user has already been told, so the
    caller simply returns rather than raising: a consent gate is a question, and "no" is an answer
    rather than an error.

    The read comes first and creates nothing. That ordering is the notice's central promise --
    cancelling has to store nothing, which it cannot do if the account row was minted to ask the
    question. It also means the fast path for an already-consented user is one indexed lookup.
    """
    user = _user_of(target)
    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        return account.id

    consent = await prompt_for_consent(target, user_id=user.id, locale=locale, timeout=timeout, parent=parent)
    if consent is NOT_ASKED:
        return None
    if consent is None:
        await _send(target, text_layout(t(locale, _("Cancelled. No account information was stored."))))
        return None

    # One write, so a receipt is never separated from the row it belongs to.
    granted = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(user.id), consent=consent)
    assert granted.id is not None, "get_or_create_identity always returns a persisted account"
    return granted.id

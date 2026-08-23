"""Asking one Discord user for informed consent, and continuing what they asked for."""

from enum import Enum
from typing import Any, cast

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
from squid.bot.i18n import t
from squid.bot.ui import CardField, localization_for, text_layout
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
"""The question was never put to the user, and they have already been told why."""

type ConsentTarget = commands.Context[Any] | discord.Interaction[Any]
"""Anywhere the bot can identify a user and answer them."""


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
                    sl.primitives.Button(t(self.locale, _("Cancel")), self._cancel, "cancel"),
                    sl.primitives.Button(t(self.locale, _("Privacy notice")), self._privacy, "privacy"),
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
        self._done.set()

    async def wait(self) -> AccountConsent | None:
        with anyio.move_on_after(self._timeout) as scope:
            await self._done.wait()
        return None if scope.cancel_called else self._consent


def _is_context(target: ConsentTarget) -> bool:
    """Whether this is a command context rather than a bare interaction."""
    return hasattr(target, "author")


def _destination(target: ConsentTarget) -> sl.discord.Destination:
    """Choose the reply transport for a consent prompt."""
    ephemeral = _default_ephemeral(target)
    if _is_context(target):
        return sl.discord.reply_to(cast(commands.Context[Any], target), ephemeral=ephemeral)
    return sl.discord.respond_to(cast(discord.Interaction[Any], target), ephemeral=ephemeral, wait=True)


async def _send(target: ConsentTarget, presentation: sl.discord.presentation.DiscordPresentation) -> None:
    """Send a plain presentation where the prompt itself would have gone."""
    await _destination(target)(presentation)


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
    if _is_context(target):
        return cast(Any, cast(commands.Context[Any], target).bot).mounts
    return cast(Any, cast(discord.Interaction[Any], target).client).mounts


def _link_credit_value(preview: LinkPreview, locale: str | None) -> str:
    credit = preview.credit
    if credit is None:
        return t(locale, _("No build credits **{username}** yet, so nothing is reattributed."), username=preview.username)
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
    """Show the notice and wait, returning the consent the user granted."""
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
                    t(locale, _("<@{user_id}> (`{user_id}`)"), user_id=user_id),
                ),
                CardField(
                    t(locale, _("Consent recorded")),
                    t(locale, _("Notice {version}, timed at the moment you agree."), version=CURRENT_CONSENT_VERSION),
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
                    t(locale, _("**{username}**\n`{uuid}`"), username=preview.username, uuid=preview.java_uuid),
                ),
                CardField(
                    t(locale, _("Discord account")),
                    t(locale, _("<@{user_id}> (`{user_id}`)"), user_id=user_id),
                ),
                CardField(t(locale, _("Build credit")), _link_credit_value(preview, locale)),
                CardField(
                    t(locale, _("Consent recorded")),
                    t(locale, _("Notice {version}, timed at the moment you agree."), version=CURRENT_CONSENT_VERSION),
                ),
            ),
            accept_label=t(locale, _("Agree and link")),
            locale=locale,
            timeout=timeout,
        )
    opened = await CONSENT_SCREEN.open(
        _registry_of(target),
        component,
        _destination(target),
        opener=Opener(user_id),
        parent=parent,
        localization=localization_for(locale),
        timeout=timeout,
    )
    if isinstance(opened, Rejected):
        await _send(target, text_layout(t(locale, _("You already have a consent prompt open. Please answer that one."))))
        return NOT_ASKED
    if not isinstance(opened, Opened):
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
    """Return the user's account id after current consent has been granted."""
    user = _user_of(target)
    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        return account.id

    consent = await prompt_for_consent(target, user_id=user.id, locale=locale, timeout=timeout, parent=parent)
    if consent is NOT_ASKED or consent is None:
        if consent is None:
            await _send(target, text_layout(t(locale, _("Cancelled. No account information was stored."))))
        return None

    granted = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(user.id), consent=consent)
    assert granted.id is not None, "get_or_create_identity always returns a persisted account"
    return granted.id

"""The panel behind `/account`.

Five commands used to answer what one screen shows: `identities` printed the linked accounts
and their ids, `unlink` took one of those ids back, `visibility` took it too — or nothing at all,
in which case it hid the whole creator page instead — and `profile` and `profile-edit` showed and
edited the card the rest of it hangs off. An identity is a thing you look at and then show, hide
or drop, so looking at it and acting on it belong to the same message (audit C5's retyping half,
the shape 5.3 and 5.4 already removed from notifications and claim review).
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import cast

import discord

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    MAX_BIO_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_LINK_LABEL_LENGTH,
    MAX_LINK_URL_LENGTH,
    MAX_PROFILE_LINKS,
    MAX_PRONOUNS_LENGTH,
    AccountConsent,
    AccountIdentity,
    AccountProfile,
    IdentityProvider,
    ProfileLink,
    ProfileUpdate,
)
from squid.accounts.errors import AccountNotFoundError
from squid.bot.consent import request_consent
from squid.bot.profile_render import own_profile_avatar
from squid.bot.ui import CardField, L
from squid.core.errors import ValidationError

SESSION_SECONDS = 300

MAX_LISTED = 25
"""A select holds 25 options, and only a long merge history reaches even a handful."""


def _provider_label(provider: IdentityProvider) -> sl.TextLike:
    match provider:
        case IdentityProvider.DISCORD:
            return L("Discord")
        case IdentityProvider.JAVA:
            return L("Minecraft (Java)")
        case IdentityProvider.BEDROCK:
            return L("Minecraft (Bedrock)")


def _identity_label(identity: AccountIdentity) -> sl.TextLike:
    provider = _provider_label(identity.provider)
    if identity.provider is IdentityProvider.DISCORD and identity.discord_id is not None:
        return L("{provider} — <@{subject}>", provider=provider, subject=identity.discord_id)
    return L(
        "{provider} — {name}",
        provider=provider,
        name=identity.display_name or identity.subject,
    )


def _error_detail(error: ValidationError) -> sl.TextLike:
    message = L(error.message, **error.message_params)
    if error.end_user_action is None:
        return message
    return L("{message} {action}", message=message, action=L(error.end_user_action))


class AccountPanel(sl.Component[sl.ComponentsV2Target]):
    """A mounted account workspace with semantic identity actions."""

    selected_id: int | None = sl.state(None)
    closed: bool = sl.state(default=False)
    # Refreshed from the service by load(), so a snapshot would only restore them stale.
    _identities: tuple[AccountIdentity, ...] = sl.state((), persist=False)
    _needs_consent: bool = sl.state(default=False, persist=False)
    # No default: the empty profile needs this instance's account id.
    _profile: AccountProfile = sl.state(persist=False)
    _profile_editor: sp.ComponentDriver[sp.EditorState, sl.ComponentsV2Target] | None = sl.state(
        None, persist=False, opaque=True
    )

    def __init__(
        self,
        *,
        accounts: AccountService,
        account_id: int,
        author_id: int,
        timeout: float = SESSION_SECONDS,
    ) -> None:
        self._accounts = accounts
        self._account_id = account_id
        self._author_id = author_id
        self._timeout = timeout
        self._profile = AccountProfile.empty(account_id)
        self._profile_editor = None
        self._root: sd.MessageRoot | None = None

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        """Re-read the account this panel is about. Also what a write calls to show its result."""
        account = await self._accounts.get_account_by_id(self._account_id)
        if account is None:
            raise AccountNotFoundError(self._account_id)
        self._identities = account.identities
        self._needs_consent = account.needs_consent_refresh
        self._profile = await self._accounts.get_profile(self._account_id)
        if self.selected is None:
            self.selected_id = None

    @property
    def identities(self) -> tuple[AccountIdentity, ...]:
        return self._identities[:MAX_LISTED]

    @property
    def selected(self) -> AccountIdentity | None:
        return next((identity for identity in self.identities if identity.id == self.selected_id), None)

    @property
    def page_hidden(self) -> bool:
        return self._profile.hidden

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.closed:
            return (sl.section(sl.heading(L("Account controls closed"))),)
        if self._profile_editor is not None:
            return (
                self.boundary(self._profile_editor, key="profile-editor"),
                sl.action_controls(
                    sl.action_control(
                        L("Cancel"),
                        self._cancel_profile_edit,
                        key="cancel-profile-edit",
                    ),
                    key="profile-editor-actions",
                ),
            )
        fields = tuple(sl.field(field.name, field.value) for field in self._fields())
        footer = self._footer()
        media = own_profile_avatar(self._profile, self._identities)
        extra_media = media[1:]
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading(self._profile.display_name or L("Your account")),
                # The bio is the card's shock absorber: truncate lets it give up characters
                # under pressure before the fields or footer lose any.
                self._profile.bio and sl.truncate(sl.paragraph(self._profile.bio)),
                sl.fields(*fields),
                bool(extra_media) and sl.media(*extra_media, key="media"),
                footer and sl.note(footer),
                thumbnail=media[0] if media else None,
            )
        ]
        if self.identities:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            _identity_label(identity),
                            key=str(identity.id),
                            description=self.identity_detail(identity),
                        )
                        for identity in self.identities
                        if identity.id is not None
                    ),
                    key="identity",
                    selection=sl.controlled(
                        (str(self.selected_id),) if self.selected_id is not None else (), self._selection_changed
                    ),
                )
            )
        identity = self.selected
        nodes.extend(
            (
                sl.toggle(
                    L("Selected identity"),
                    key="identity_visibility",
                    on=sl.controlled(identity is not None and identity.is_public, self._toggle_identity),
                    on_label=L("Shown on page"),
                    off_label=L("Hidden from page"),
                    available=identity is not None,
                ),
                sl.toggle(
                    L("Creator page"),
                    key="page_visibility",
                    on=sl.controlled(not self.page_hidden, self._toggle_page),
                    on_label=L("Shown"),
                    off_label=L("Hidden"),
                ),
            )
        )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    L("Unlink"),
                    self._unlink,
                    key="unlink",
                    tone=sl.Tone.DANGER,
                    available=self.selected is not None,
                    guard=sp.guards.confirm(self._unlink_warning()),
                ),
                sl.action_control(
                    L("Edit page"),
                    self._edit_page,
                    key="edit_page",
                    emphasis=sl.semantic.Emphasis.STRONG,
                ),
                sl.action_control(L("Close"), self._close, key="close"),
                key="account-actions",
            )
        )
        return tuple(nodes)

    async def _selection_changed(self, event: sl.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])

    async def _toggle_identity(self, event: sl.ToggleEvent) -> None:
        identity = self.selected
        if identity is None or identity.id is None:
            return
        identity_id, is_public = identity.id, event.value

        async def apply() -> None:
            await self._accounts.set_identity_visibility(self._account_id, identity_id, is_public=is_public)
            await self._reload()

        await self._with_consent(event, apply)

    async def _toggle_page(self, event: sl.ToggleEvent) -> None:
        hidden = not event.value

        async def apply() -> None:
            await self._accounts.update_profile(self._account_id, ProfileUpdate(hidden=hidden))
            await self._reload()

        await self._with_consent(event, apply)

    async def _unlink(self, event: sl.PressEvent) -> None:
        """Remove the selected identity. The reader has already agreed to this."""
        identity = self.selected
        if identity is None or identity.id is None:
            return
        await event.acknowledge()
        removed = await self._accounts.unlink_identity(self._account_id, identity.id)
        await self._reload()
        await event.notice(
            L(
                "Unlinked {identity}. Any build credit you hold is unaffected.",
                identity=_identity_label(removed),
            )
        )

    async def _edit_page(self, event: sl.PressEvent) -> None:
        async def apply() -> None:
            self._profile_editor = self._build_profile_editor()

        await self._with_consent(event, apply)

    def _build_profile_editor(self) -> sp.ComponentDriver[sp.EditorState, sl.ComponentsV2Target]:
        profile_section = sp.EditorSection.from_form(
            "profile",
            L("Profile"),
            sl.forms.FormSpec(
                L("Edit profile"),
                (
                    sl.forms.TextField(
                        key="display_name",
                        label=L("Display name"),
                        required=False,
                        maximum=MAX_DISPLAY_NAME_LENGTH,
                    ),
                    sl.forms.TextField(
                        key="pronouns",
                        label=L("Pronouns"),
                        required=False,
                        maximum=MAX_PRONOUNS_LENGTH,
                    ),
                    sl.forms.TextAreaField(
                        key="bio",
                        label=L("Bio"),
                        required=False,
                        maximum=MAX_BIO_LENGTH,
                    ),
                ),
            ),
        )
        links = sp.CollectionEditor(
            L("Links"),
            create=sl.forms.FormSpec(
                L("Profile link"),
                (
                    sl.forms.TextField(
                        key="label",
                        label=L("Label"),
                        maximum=MAX_LINK_LABEL_LENGTH,
                    ),
                    sl.forms.TextField(
                        key="url",
                        label=L("HTTPS URL"),
                        maximum=MAX_LINK_URL_LENGTH,
                    ),
                ),
                validator=self._validate_link,
            ),
            label=lambda value: str(value["label"]),
            minimum=0,
            maximum=MAX_PROFILE_LINKS,
        )
        links_section = sp.EditorSection.from_pattern(
            "links",
            L("Links"),
            links,
            load=lambda value: links.initial_from(cast(Iterable[Mapping[str, object]], value)),
            dump=links.values,
            summary=lambda value: L("{count} links", count=len(value)),
            issues=lambda state: (sl.forms.FormError(message) for message in links.errors(state)),
        )
        editor = sp.Editor[sl.ComponentsV2Target](
            L("Edit your creator page"),
            (profile_section, links_section),
            preview=self._profile_preview,
            commit_label=L("Save profile"),
            validate=self._validate_profile_editor,
        )
        initial: sp.EditorValues = {
            "profile": {
                "display_name": self._profile.display_name,
                "pronouns": self._profile.pronouns,
                "bio": self._profile.bio,
            },
            "links": tuple({"label": link.label, "url": link.url} for link in self._profile.links),
        }
        return editor.build_component(initial=initial, on_commit=self._profile_committed)

    def _validate_link(self, values: Mapping[str, object]) -> tuple[sl.forms.FormIssue, ...]:
        try:
            ProfileLink.parse(str(values["label"]), str(values["url"]))
        except ValidationError as error:
            return (sl.forms.FormError(_error_detail(error)),)
        return ()

    def _raw_profile_update(self, values: sp.EditorValues) -> ProfileUpdate:
        profile = cast(Mapping[str, object], values["profile"])
        links = cast(Iterable[Mapping[str, object]], values["links"])
        return ProfileUpdate(
            display_name=cast(str | None, profile["display_name"]),
            pronouns=cast(str | None, profile["pronouns"]),
            bio=cast(str | None, profile["bio"]),
            links=tuple(ProfileLink(str(link["label"]), str(link["url"])) for link in links),
        )

    def _profile_update(self, values: sp.EditorValues) -> ProfileUpdate:
        return self._raw_profile_update(values).validated()

    def _validate_profile_editor(self, values: sp.EditorValues) -> tuple[sl.forms.FormIssue, ...]:
        try:
            self._profile_update(values)
        except ValidationError as error:
            return (sl.forms.FormError(_error_detail(error)),)
        return ()

    def _profile_preview(self, values: sp.EditorValues) -> sl.LayoutNode[sl.ComponentsV2Target]:
        draft = self._raw_profile_update(values).apply(self._profile)
        fields = tuple(sl.field(field.name, field.value) for field in self._profile_fields(draft))
        return sl.section(
            sl.heading(draft.display_name or L("Your account")),
            draft.bio and sl.truncate(sl.paragraph(draft.bio)),
            sl.fields(*fields) if fields else None,
        )

    async def _profile_committed(
        self,
        event: sp.TransitionEvent[sp.EditorState],
        values: sp.EditorValues,
        _changed: frozenset[str],
    ) -> None:
        await self._accounts.update_profile(self._account_id, self._profile_update(values))
        await self._refresh()
        self._profile_editor = None
        await event.source.notice(L("Profile saved."))

    async def _cancel_profile_edit(self, _event: sl.PressEvent) -> None:
        self._profile_editor = None

    async def _with_consent(self, event: sl.ActionEvent, work: Callable[[], Awaitable[None]]) -> None:
        """Run `work` now, or once the reader has agreed to be recorded.

        Opening the notice ends this press: `request_consent` returns as soon as it is on
        screen, so the panel's transaction closes and its dispatch lock is released rather
        than being held for as long as the reader takes to read. `work` then runs inside the
        prompt's own press, and the panel redraws through its own handle -- never through the
        prompt's interaction, which addresses the prompt's message rather than the panel's.
        """
        if not self._needs_consent:
            await work()
            return
        message_root = sd.responder(event).message_root

        async def answered(_prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
            if consent is None:
                # Cancelled. The notice said agreeing is what stores anything, and the prompt
                # closing is the whole answer; the panel already shows the unchanged truth.
                return
            await self._accounts.grant_current_consent(self._account_id)
            self._needs_consent = False
            await work()
            await message_root.schedule()

        await request_consent(
            sd.native(event),
            user_id=self._author_id,
            on_answer=answered,
            parent=message_root,
        )

    async def _reload(self) -> None:
        await self._refresh()
        self.invalidate()

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def _fields(self) -> list[CardField]:
        fields = self._profile_fields(self._profile)
        fields += [CardField(_identity_label(identity), self.identity_detail(identity)) for identity in self.identities]
        if not fields:
            fields.append(CardField(L("Linked accounts"), L("_None yet._")))
        fields.append(
            CardField(
                L("Creator page"),
                L("Hidden") if self.page_hidden else L("Public"),
            )
        )
        return fields

    @staticmethod
    def _profile_fields(profile: AccountProfile) -> list[CardField]:
        fields: list[CardField] = []
        if profile.pronouns:
            fields.append(CardField(L("Pronouns"), profile.pronouns))
        if profile.links:
            links = "\n".join(f"[{link.label}]({link.url})" for link in profile.links)
            fields.append(CardField(L("Links"), links))
        return fields

    def identity_detail(self, identity: AccountIdentity) -> sl.TextLike:
        return L(
            "{visibility} · verified {age}",
            visibility=L("shown publicly") if identity.is_public else L("hidden"),
            age=(
                sl.md(discord.utils.format_dt(identity.verified_at.to_stdlib(), style="R"))
                if identity.verified_at is not None
                else L("unknown")
            ),
        )

    def _unlink_warning(self) -> sl.TextLike:
        """What the reader is agreeing to, asked before the press rather than after it."""
        identity = self.selected
        if identity is None:
            return L("Remove this linked account?")
        warning = L(
            "Remove {identity}? Any build credit you hold is unaffected.",
            identity=_identity_label(identity),
        )
        if identity.provider is IdentityProvider.DISCORD and identity.discord_id == self._author_id:
            return L(
                "{warning} {consequence}",
                warning=warning,
                consequence=L("This is the Discord account you are using now. The bot will stop recognising you here."),
            )
        return warning

    def _footer(self) -> sl.TextLike | None:
        if self.page_hidden:
            return L(
                "A hidden page still lists the creator names you hold, because that credit is what attributes your builds.",
            )
        return None

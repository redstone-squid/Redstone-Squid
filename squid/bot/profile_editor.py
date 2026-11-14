"""Stage, validate, and preview creator profile edits before saving."""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from typing import cast, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.accounts.domain import (
    MAX_BIO_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_LINK_LABEL_LENGTH,
    MAX_LINK_URL_LENGTH,
    MAX_PROFILE_LINKS,
    MAX_PRONOUNS_LENGTH,
    ProfileLink,
    ProfileUpdate,
)
from squid.accounts.models import AccountProfile
from squid.bot.edit_history import history_controls, track_local_edits
from squid.bot.profile_render import own_profile_fields
from squid.bot.ui import tr
from squid.core.errors import ValidationError
from squid_i18n import md_tr


def _link_count(count: int) -> sl.text.Text:
    return md_tr(t"account-view-links = {count} links")


class ProfileEditor(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Own a profile draft and its local history until save or cancel."""

    _active: bool = sl.state(True, persist=False)
    history: sl.runtime.History = sl.runtime.history(limit=20)

    def __init__(
        self,
        profile: AccountProfile,
        *,
        save: Callable[[ProfileUpdate], Awaitable[None]],
        close: Callable[[], None],
    ) -> None:
        self._profile = profile
        self._save = save
        self._close = close
        self.editor = self._build_profile_editor()

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            self.editor,
            *history_controls(self.history),
            sl.action_controls(
                sl.action_control(
                    tr(t"account-view-cancel = Cancel"),
                    self._cancel,
                    key="cancel-profile-edit",
                ),
                key="profile-editor-actions",
            ),
        )

    async def _cancel(self, _event: sd.PressEvent) -> None:
        self._active = False
        self._close()

    def _build_profile_editor(self) -> sp.ComponentDriver[sp.EditorState, sl.ComponentsV2Target, sd.Request]:
        profile_section = sp.EditorSection.from_form(
            "profile",
            tr(t"account-view-profile = Profile"),
            sl.forms.FormSpec(
                tr(t"account-view-edit-profile = Edit profile"),
                (
                    sl.forms.TextField(
                        key="display_name",
                        label=tr(t"account-view-display-name = Display name"),
                        required=False,
                        maximum=MAX_DISPLAY_NAME_LENGTH,
                    ),
                    sl.forms.TextField(
                        key="pronouns",
                        label=tr(t"account-view-pronouns = Pronouns"),
                        required=False,
                        maximum=MAX_PRONOUNS_LENGTH,
                    ),
                    sl.forms.TextAreaField(
                        key="bio",
                        label=tr(t"account-view-bio = Bio"),
                        required=False,
                        maximum=MAX_BIO_LENGTH,
                    ),
                ),
            ),
        )
        links = sp.CollectionEditor(
            tr(t"account-view-links-2 = Links"),
            create=sl.forms.FormSpec(
                tr(t"account-view-profile-link = Profile link"),
                (
                    sl.forms.TextField(
                        key="label",
                        label=tr(t"account-view-label = Label"),
                        maximum=MAX_LINK_LABEL_LENGTH,
                    ),
                    sl.forms.TextField(
                        key="url",
                        label=tr(t"account-view-https-url = HTTPS URL"),
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
            tr(t"account-view-links-2 = Links"),
            links,
            load=lambda value: links.initial_from(cast(Iterable[Mapping[str, object]], value)),
            dump=links.values,
            summary=lambda value: _link_count(len(value)),
            issues=lambda state: (sl.forms.FormError(message) for message in links.errors(state)),
        )
        editor = sp.Editor[sl.ComponentsV2Target, sd.Request](
            tr(t"account-view-edit-your-creator-page = Edit your creator page"),
            (profile_section, links_section),
            preview=self._profile_preview,
            commit_label=tr(t"account-view-save-profile = Save profile"),
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
        self.history.clear()
        driver = editor.build_component(initial=initial, on_commit=self._profile_committed)
        track_local_edits(
            self.history,
            driver,
            tr(t"account-history-profile-draft = Edit profile draft"),
            select=editor.values,
            enabled=lambda: self._active,
            restore=lambda state, values: replace(
                state,
                sections=tuple(
                    replace(old, state=new.state)
                    for old, new in zip(state.sections, editor.initial_from(values).sections, strict=True)
                ),
            ),
        )
        return driver

    def _validate_link(self, values: Mapping[str, object]) -> tuple[sl.forms.FormIssue, ...]:
        try:
            ProfileLink.parse(str(values["label"]), str(values["url"]))
        except ValidationError as error:
            return (sl.forms.FormError(error.public_text()),)
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
            return (sl.forms.FormError(error.public_text()),)
        return ()

    def _profile_preview(self, values: sp.EditorValues) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        draft = self._raw_profile_update(values).apply(self._profile)
        fields = tuple(sl.field(field.name, field.value) for field in own_profile_fields(draft))
        return sl.section(
            sl.heading(draft.display_name or tr(t"account-view-your-account = Your account")),
            draft.bio and sl.paragraph(draft.bio).truncate(),
            sl.fields(*fields) if fields else None,
        )

    async def _profile_committed(
        self,
        event: sp.TransitionEvent[sp.EditorState],
        values: sp.EditorValues,
        _changed: frozenset[str],
    ) -> None:
        await self._save(self._profile_update(values))
        self.history.clear()
        self._active = False
        self._close()
        await event.source.notice(tr(t"account-view-profile-saved = Profile saved."))

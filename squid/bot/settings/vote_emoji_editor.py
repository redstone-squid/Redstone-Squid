"""Prepare an emoji preset as rows before saving the complete valid preset."""

from dataclasses import replace
from functools import partial
from typing import Any, override
from uuid import uuid4

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import tr
from squid.voting.domain import VoteChoice, VoteKind, VoteOption, normalize_vote_options
from squid_ui.ext.discord.target_types import ComponentsV2Target


class VoteEmojiEditor(sd.Screen[tuple[VoteOption, ...]]):
    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("vote-emoji-editor"))
    options: tuple[VoteOption, ...] = sl.state(())

    def __init__(self, guild: discord.Guild, kind: VoteKind, options: tuple[VoteOption, ...]) -> None:
        self.guild = guild
        self.kind = kind
        self.options = options

    def _form(self, option: VoteOption | None = None) -> sl.forms.FormSpec[sd.Request]:
        fields: list[sl.forms.FormField] = [
            sl.forms.TextField(
                key="emoji",
                label=tr(t"vote-row-emoji = Emoji"),
                default=None if option is None else option.emoji,
                maximum=100,
            )
        ]
        if self.kind is VoteKind.GENERIC:
            fields.append(
                sl.forms.TextField(
                    key="label",
                    label=tr(t"vote-row-label = Option label"),
                    default=None if option is None else option.label,
                    maximum=100,
                )
            )
        else:
            fields.append(
                sl.forms.ChoiceField(
                    key="choice",
                    label=tr(t"vote-row-choice = Choice"),
                    default=VoteChoice.APPROVE if option is None else option.choice,
                    options=(
                        sl.forms.ChoiceOption("approve", tr(t"vote-row-approve = Approve"), VoteChoice.APPROVE),
                        sl.forms.ChoiceOption("deny", tr(t"vote-row-deny = Deny"), VoteChoice.DENY),
                    ),
                )
            )
        return sl.forms.FormSpec[sd.Request[Any, Any]](tr(t"vote-row-title = Vote emoji"), tuple(fields))

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        return (
            sl.heading(tr(t"vote-rows-title = Vote emojis")),
            *(
                sl.stack(
                    sl.paragraph(f"{option.emoji} — {option.label or option.choice.value}"),
                    sl.action_controls(
                        sl.form(
                            tr(t"vote-row-edit = Edit"),
                            self._form(option),
                            key=f"edit-{index}",
                            on_submit=partial(self._edit, index),
                        ),
                        sl.action_control(
                            tr(t"vote-row-remove = Remove"), partial(self._remove, index), key=f"remove-{index}"
                        ),
                        key=f"row-{index}",
                    ),
                )
                for index, option in enumerate(self.options)
            ),
            sl.action_controls(
                sl.form(tr(t"vote-row-add = Add emoji"), self._form(), key="add", on_submit=partial(self._edit, None)),
                sl.action_control(tr(t"vote-rows-save = Save emojis"), self._save, key="save"),
                sl.action_control(tr(t"vote-rows-cancel = Cancel"), self._cancel, key="cancel"),
                key="actions",
            ),
        )

    async def _edit(self, index: int | None, event: sd.SubmitEvent) -> None:
        emoji = str(event.values["emoji"])
        parsed = discord.PartialEmoji.from_str(emoji)
        if parsed.is_custom_emoji():
            custom = self.guild.get_emoji(parsed.id or 0)
            if custom is None or not custom.is_usable():
                await event.notice(tr(t"vote-row-emoji-unavailable = That custom emoji is inaccessible."))
                return
        choice = VoteChoice.GENERIC if self.kind is VoteKind.GENERIC else VoteChoice(str(event.values["choice"]))
        label = str(event.values["label"]) if self.kind is VoteKind.GENERIC else None
        if index is None:
            self.options = (
                *self.options,
                VoteOption(
                    emoji,
                    choice,
                    guild_id=self.guild.id,
                    label=label,
                    identifier=uuid4().hex if choice is VoteChoice.GENERIC else choice.value,
                    position=len(self.options),
                ),
            )
        else:
            before = self.options[index]
            after = replace(
                before,
                emoji=emoji,
                choice=choice,
                label=label,
                identifier=before.identifier if choice is VoteChoice.GENERIC else choice.value,
            )
            self.options = (*self.options[:index], after, *self.options[index + 1 :])

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self.options = (*self.options[:index], *self.options[index + 1 :])

    async def _save(self, event: sd.PressEvent) -> None:
        options = normalize_vote_options(
            tuple(replace(option, position=index) for index, option in enumerate(self.options)), kind=self.kind
        )
        self.complete(event, options)

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)

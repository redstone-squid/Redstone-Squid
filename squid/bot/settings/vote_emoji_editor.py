"""Prepare an emoji preset as rows before saving the complete valid preset."""

from functools import partial
from typing import Any, override
from uuid import uuid4

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import tr
from squid.core.voting.domain import VoteAlias, VoteChoice, VoteKind, VoteMenu, VoteOption
from squid_ui.ext.discord.target_types import ComponentsV2Target


class VoteEmojiEditor(sd.Screen[VoteMenu]):
    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("vote-emoji-editor"))
    rows: tuple[tuple[VoteOption, VoteAlias], ...] = sl.state(())
    """One row per emoji, paired with the option it selects."""

    def __init__(self, guild: discord.Guild, kind: VoteKind, menu: VoteMenu | None) -> None:
        self.guild = guild
        self.kind = kind
        self.rows = () if menu is None else menu.rows()

    def _form(self, row: tuple[VoteOption, VoteAlias] | None = None) -> sl.forms.FormSpec[sd.Request]:
        option, alias = (None, None) if row is None else row
        fields: list[sl.forms.FormField[Any, Any]] = [
            sl.forms.TextField(
                key="emoji",
                label=tr(t"vote-row-emoji = Emoji"),
                default=None if alias is None else alias.emoji,
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
                    sl.paragraph(f"{alias.emoji} — {option.label or option.choice.value}"),
                    sl.action_controls(
                        sl.form(
                            tr(t"vote-row-edit = Edit"),
                            self._form((option, alias)),
                            key=f"edit-{index}",
                            on_submit=partial(self._edit, index),
                        ),
                        sl.action_control(
                            tr(t"vote-row-remove = Remove"), partial(self._remove, index), key=f"remove-{index}"
                        ),
                        key=f"row-{index}",
                    ),
                )
                for index, (option, alias) in enumerate(self.rows)
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
        before = None if index is None else self.rows[index][0]
        option_id = (before.id if before is not None else uuid4().hex) if choice is VoteChoice.GENERIC else choice.value
        # An edit keeps the option's identity and multiplier; only what the row shows changes.
        multiplier = before.multiplier if before is not None and before.id == option_id else 1.0
        option = VoteOption(option_id, choice, label=label, multiplier=multiplier)
        row = (option, VoteAlias(option_id, emoji, self.guild.id))
        if index is None:
            self.rows = (*self.rows, row)
        else:
            self.rows = (*self.rows[:index], row, *self.rows[index + 1 :])

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self.rows = (*self.rows[:index], *self.rows[index + 1 :])

    async def _save(self, event: sd.PressEvent) -> None:
        self.complete(event, VoteMenu.of(self.rows).validated(self.kind))

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)

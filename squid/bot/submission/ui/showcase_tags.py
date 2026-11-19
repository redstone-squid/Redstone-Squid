"""Set the values of chosen showcase tags, such as Speed: 1.5.

Tags are chosen with the section editor's paged picker; this editor lists the chosen tags and
opens a modal for each one that takes a value.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, override

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import tr
from squid.builds.domain import ShowcaseTag, read_showcase_tags
from squid.core.errors import JSONValue, ValidationError
from squid.submissions.domain import ChoiceOption, FormField
from squid.tags.domain import TagValueType
from squid.tags.domain.values import assignment_value, coerce_assignment_value
from squid_i18n import Text, md_tr
from squid_ui.content.text import md
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    from squid.bot.submission.ui.sections import AnswerWorkspace


_MAX_SELECT_OPTIONS = 25
_VALUE_PREVIEW_LENGTH = 100


def read_answer(value: JSONValue) -> tuple[ShowcaseTag, ...]:
    """Read a showcase tag answer, treating a malformed one as empty so the picker can replace it."""
    try:
        return read_showcase_tags(value or [])
    except ValidationError:
        return ()


def tags_text(tags: Sequence[ShowcaseTag], options: Mapping[str, ChoiceOption]) -> Text:
    """List tags as "Compact, Speed: 1.5", flagging tags that still need a value."""
    parts: list[Text] = []
    for tag in tags:
        option = options.get(tag.key)
        label = tag.key if option is None else option.label
        if tag.value is not None:
            value = (
                tag.value if len(tag.value) <= _VALUE_PREVIEW_LENGTH else tag.value[: _VALUE_PREVIEW_LENGTH - 1] + "…"
            )
            parts.append(md(t"{label}: {value}"))
        elif option is not None and option.value_type is not None:
            parts.append(md_tr(t"showcase-tags-needs-value = {label}: ⚠️ *Needs a value*"))
        else:
            parts.append(md(t"{label}"))
    return Text.join(", ", parts)


class ShowcaseTagEditor(sl.Component[ComponentsV2Target, sd.Request]):
    """The chosen tags of one showcase tag field, and a menu to set the value of tags that take one."""

    def __init__(self, workspace: AnswerWorkspace, field: FormField) -> None:
        self.workspace = workspace
        self.field = field

    @property
    def options(self) -> dict[str, ChoiceOption]:
        return {option.value: option for option in self.workspace.option_sets.get(self.field.option_source or "", ())}

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        tags = read_answer(self.workspace.answers.get(self.field.id))
        options = self.options
        valued = [tag for tag in tags if (option := options.get(tag.key)) is not None and option.value_type]
        plain = [tag for tag in tags if tag not in valued]
        lines = [*((tags_text(plain, options),) if plain else ()), *(tags_text((tag,), options) for tag in valued)]
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [sd.v2.text(Text.join("\n", lines))]
        if valued:
            # One menu, rather than a paged list of buttons, keeps the message a fixed size beside the
            # paged picker. It holds 25 options, so tags that still need a value come first.
            shown = sorted(valued, key=lambda tag: tag.value is not None)[:_MAX_SELECT_OPTIONS]
            nodes.append(
                sd.v2.select(
                    *(
                        sd.v2.option(
                            options[tag.key].label[:100],
                            tag.key,
                            description=tr(t"showcase-tags-needs-value-short = Needs a value")
                            if tag.value is None
                            else tag.value[:100],
                        )
                        for tag in shown
                    ),
                    on_select=self._open_value_form,
                    key=f"values_{self.field.id}",
                    placeholder=tr(t"showcase-tags-set-value = Set a tag's value…"),
                )
            )
        return tuple(nodes)

    async def _open_value_form(self, event: sd.SelectionEvent) -> None:
        key = event.values[0]
        tags = read_answer(self.workspace.answers.get(self.field.id))
        tag = next((tag for tag in tags if tag.key == key), None)
        option = self.options.get(key)
        if tag is None or option is None or option.value_type is None:
            return
        await event.present_form(
            sl.forms.FormSpec[sd.Request[Any, Any]](option.label[:45], (_value_field(option, tag.value),)),
            key=f"showcase-value-{key}",
            on_submit=partial(self._set_value, tags, key, self.workspace.revision),
        )

    async def _set_value(self, tags: tuple[ShowcaseTag, ...], key: str, revision: int, event: sl.SubmitEvent) -> None:
        value = str(event.values["value"])
        changed = [ShowcaseTag(key, value) if tag.key == key else tag for tag in tags]
        await self.workspace.save_values(
            event.transaction, {self.field.id: [tag.to_json() for tag in changed]}, base_revision=revision
        )


def _value_field(option: ChoiceOption, value: str | None) -> sl.forms.FormField[Any, Any]:
    label = tr(t"showcase-tags-value = Value")
    if option.value_type == TagValueType.BOOLEAN:
        return sl.forms.ChoiceField[str, Any](
            key="value",
            label=label,
            default=value,
            options=(
                sl.forms.ChoiceOption("true", tr(t"draft-editor-yes = Yes"), "true"),
                sl.forms.ChoiceOption("false", tr(t"draft-editor-no = No"), "false"),
            ),
        )
    numeric = option.value_type == TagValueType.NUMERIC
    return _TagValueField(
        key="value",
        label=label,
        description=tr(t"showcase-tags-number-help = A number, such as 1.5.") if numeric else None,
        default=value,
        maximum=300,
        display_name=option.label,
        value_type=TagValueType(option.value_type),
    )


@dataclass(frozen=True, slots=True)
class _TagValueField(sl.forms.TextField[Any]):
    """A text or numeric tag value, checked the way the tag will read it.

    It carries the tag's name and value type, so it can stand in for the tag definition.
    """

    display_name: str = ""
    value_type: TagValueType = TagValueType.TEXT

    @override
    def parse(self, raw: object) -> str | None:
        text = super().parse(raw)
        try:
            return assignment_value(coerce_assignment_value(self, text))
        except ValidationError as error:
            raise sl.forms.FormValueError(error.public_text()) from error

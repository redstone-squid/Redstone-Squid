"""Build and validate a Slack modal's portable form fields without another planner."""

import hashlib
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from string.templatelib import Template
from typing import Any

from squid_i18n import Text as TextExpression
from squid_ui import scene
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.document import DocumentLike, as_document
from squid_ui.content.text import Localization, resolve_text
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.slack.limits import SlackModalLimits
from squid_ui.factories import is_portable_node
from squid_ui.interactions import forms
from squid_ui.planning.composition import CandidateViolation, LocalCapacityExceeded, OccurrenceId


def _nested_form(node: object) -> bool:
    if isinstance(node, sem.FormTrigger):
        return True
    if not is_dataclass(node):
        return False
    for held in dataclass_fields(node):
        value = getattr(node, held.name)
        if is_portable_node(value) and _nested_form(value):
            return True
        if isinstance(value, tuple) and any(is_portable_node(item) and _nested_form(item) for item in value):
            return True
    return False


def modal_form(rendered: DocumentLike[Any]) -> sem.FormTrigger:
    document = as_document(rendered)
    roots = tuple(child for child in document.children if isinstance(child, sem.FormTrigger))
    if len(roots) != 1:
        message = "Slack modal planning requires exactly one top-level sl.form()"
        raise LayoutInvariantError(message)
    if any(_nested_form(child) for child in document.children if child is not roots[0]):
        message = "Slack modal forms must be top-level, not nested in surrounding content"
        raise LayoutInvariantError(message)
    return roots[0]


@dataclass
class SlackFormRenderer:
    limits: SlackModalLimits
    localization: Localization
    identity: OccurrenceId
    violations: list[CandidateViolation] = field(default_factory=list)

    def plain(self, value: str, path: str, *, capacity: int) -> scene.SlackText:
        if len(value) > capacity:
            self.violations.append(LocalCapacityExceeded(self.identity, path, len(value), capacity))
        return scene.SlackText(value, scene.SlackTextKind.PLAIN, emoji=True, verbatim=None)

    def text(self, value: str, path: str, *, capacity: int) -> scene.SlackText:
        return self.plain(value, path, capacity=capacity)

    def _stable_id(self, value: str, path: str) -> None:
        if not value or len(value) > self.limits.components.action_id:
            message = f"{path}: Slack action ids must contain 1-{self.limits.components.action_id} characters"
            raise LayoutInvariantError(message)

    def blocks[ContextT](self, trigger: forms.FormBinding[ContextT], path: str) -> tuple[scene.SlackBlock, ...]:
        spec = trigger.spec
        blocks: list[scene.SlackBlock] = []
        for index, item in enumerate(spec.items):
            item_path = f"{path}.{index}"
            if isinstance(item, forms.FormText):
                content = resolve_text(item.content, self.localization).content
                blocks.append(scene.SlackSection(self.text(content, item_path, capacity=3000)))
                continue
            blocks.append(self._form_field(trigger.key, spec, item, item_path))
        return tuple(blocks)

    def _form_field[ContextT](
        self,
        form_key: str,
        spec: forms.FormSpec[ContextT],
        field: forms.FormField[Any],
        path: str,
    ) -> scene.SlackInput:
        self._stable_id(field.key, path)
        if field.label is None:
            message = f"{path}: adapted form field has no label"
            raise LayoutInvariantError(message)
        label_value = resolve_text(field.label, self.localization).content
        label = self.plain(label_value, path, capacity=2000)
        hint = (
            None
            if field.description is None
            else self.plain(resolve_text(field.description, self.localization).content, path, capacity=2000)
        )
        prefill = spec.prefill_for(field)
        element = self._form_element(field, prefill, path)
        block_id = f"{form_key}:{field.key}"
        if len(block_id) > self.limits.components.block_id:
            digest = hashlib.blake2s(block_id.encode(), digest_size=8).hexdigest()
            block_id = f"form:{digest}"
        return scene.SlackInput(block_id, label, element, optional=not field.required, hint=hint)

    def _form_element(self, field: forms.FormField[Any], prefill: object, path: str) -> scene.SlackInputElement:
        placeholder_value: object = getattr(field, "placeholder", None)
        if placeholder_value is not None and not isinstance(placeholder_value, str | TextExpression | Template):
            message = f"{path}: form field placeholder is not text"
            raise LayoutInvariantError(message)
        placeholder = (
            None
            if placeholder_value is None
            else self.plain(
                resolve_text(placeholder_value, self.localization).content,
                path,
                capacity=self.limits.components.placeholder,
            )
        )
        initial = None if prefill is None else str(prefill)
        if isinstance(field, forms.TextAreaField):
            return scene.SlackTextInput(
                field.key,
                initial,
                placeholder,
                multiline=True,
                minimum_length=field.minimum,
                maximum_length=field.maximum,
            )
        if isinstance(field, forms.TextField | forms.DurationField | forms.DateTimeField | forms.ZonedDateTimeField):
            minimum = field.minimum if isinstance(field, forms.TextField) else None
            maximum = field.maximum if isinstance(field, forms.TextField) else None
            return scene.SlackTextInput(
                field.key,
                initial,
                placeholder,
                minimum_length=minimum,
                maximum_length=maximum,
            )
        if isinstance(field, forms.IntField | forms.FloatField):
            return scene.SlackNumberInput(
                field.key,
                initial,
                decimal_allowed=isinstance(field, forms.FloatField),
                minimum=None if field.minimum is None else str(field.minimum),
                maximum=None if field.maximum is None else str(field.maximum),
            )
        if isinstance(field, forms.DateField):
            return scene.SlackDatePicker(field.key, initial, placeholder)
        if isinstance(field, forms.TimeField):
            return scene.SlackTimePicker(field.key, initial, placeholder)
        if isinstance(field, forms.BoolField):
            option = scene.SlackOption(self.plain("Yes", path, capacity=75), "true")
            selected = ("true",) if bool(prefill) else ()
            return scene.SlackCheckboxes(field.key, (option,), selected)
        if isinstance(field, forms.ScaleField):
            options = tuple(
                scene.SlackOption(
                    self.plain(
                        resolve_text(field.label_for(value), self.localization).content,
                        path,
                        capacity=self.limits.components.option_label,
                    ),
                    str(value),
                )
                for value in field.points
            )
            return self._choice_element(field.key, options, initial, multiple=False, path=path)
        if isinstance(field, forms.ChoiceField | forms.MultiChoiceField):
            options = tuple(
                scene.SlackOption(
                    self.plain(
                        resolve_text(option.label, self.localization).content,
                        path,
                        capacity=self.limits.components.option_label,
                    ),
                    option.key,
                    None
                    if option.description is None
                    else self.plain(
                        resolve_text(option.description, self.localization).content,
                        path,
                        capacity=self.limits.components.option_description,
                    ),
                )
                for option in field.options
            )
            selected = tuple(prefill) if isinstance(prefill, tuple) else (() if prefill is None else (str(prefill),))
            return self._choice_element(
                field.key,
                options,
                selected if isinstance(field, forms.MultiChoiceField) else initial,
                multiple=isinstance(field, forms.MultiChoiceField),
                path=path,
            )
        message = f"{path}: Slack cannot represent form field {type(field).__name__}"
        raise LayoutInvariantError(message)

    def _choice_element(
        self,
        key: str,
        options: tuple[scene.SlackOption, ...],
        initial: str | tuple[str, ...] | None,
        *,
        multiple: bool,
        path: str,
    ) -> scene.SlackInputElement:
        if len(options) > self.limits.components.select_options:
            message = f"{path}: Slack form choices cannot exceed 100 options"
            raise LayoutInvariantError(message)
        if len(options) <= 10:
            if multiple:
                if initial is not None and not isinstance(initial, tuple):
                    message = f"{path}: multi-choice initial value is not a tuple"
                    raise LayoutInvariantError(message)
                selected = initial or ()
                return scene.SlackCheckboxes(key, options, selected)
            if isinstance(initial, tuple):
                message = f"{path}: single-choice initial value is a tuple"
                raise LayoutInvariantError(message)
            return scene.SlackRadioButtons(key, options, initial)
        if multiple:
            if initial is not None and not isinstance(initial, tuple):
                message = f"{path}: multi-choice initial value is not a tuple"
                raise LayoutInvariantError(message)
            selected_values = initial or ()
        else:
            if isinstance(initial, tuple):
                message = f"{path}: single-choice initial value is a tuple"
                raise LayoutInvariantError(message)
            selected_values = () if initial is None else (initial,)
        return scene.SlackSelect(
            action_id=key,
            kind=scene.SlackSelectKind.STATIC,
            options=options,
            initial_values=selected_values,
            minimum=0,
            maximum=len(options) if multiple else 1,
        )


def form_texts(value: object) -> tuple[scene.SlackText, ...]:
    """Collect text carried by a form block, including option and placeholder labels."""
    if isinstance(value, scene.SlackText):
        return (value,)
    if isinstance(value, tuple):
        return tuple(text for item in value for text in form_texts(item))
    if is_dataclass(value) and not isinstance(value, type):
        return tuple(text for held in dataclass_fields(value) for text in form_texts(getattr(value, held.name)))
    return ()

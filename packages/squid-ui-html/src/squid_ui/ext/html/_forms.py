"""Render adapted portable form fields as native HTML controls."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, cast

from squid_ui import scene
from squid_ui.content.text import Localization, TextLike, resolve_text
from squid_ui.ext.html._text import html_text
from squid_ui.interactions import forms as form_types


def _attributes(
    *values: tuple[scene.HtmlAttributeName, scene.HtmlAttributeValue | None],
) -> tuple[scene.HtmlAttribute, ...]:
    return tuple(scene.HtmlAttribute(name, value) for name, value in values if value is not None)


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date | datetime | time):
        return value.isoformat()
    return "" if value is None else str(value)


@dataclass(frozen=True, slots=True)
class HtmlFormRenderer:
    localization: Localization

    def render[ContextT](self, binding: form_types.FormBinding[ContextT]) -> scene.HtmlElement:
        spec = binding.spec
        children: list[scene.HtmlNode] = [self.element(scene.HtmlTag.H3, self.text(spec.title))]
        for item in spec.items:
            if isinstance(item, form_types.FormText):
                children.append(self.element(scene.HtmlTag.P, self.text(item.content)))
            else:
                children.append(self._form_field(binding.key, spec, item))
        action = scene.HtmlActionRef(binding.key, binding.mode)
        children.append(
            self.element(
                scene.HtmlTag.BUTTON,
                self.text(binding.label),
                attributes=_attributes((scene.HtmlAttributeName.TYPE, "submit")),
                action=action,
                form=scene.HtmlFormRef(binding.key),
            )
        )
        return self.element(
            scene.HtmlTag.FORM,
            *children,
            attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-form")),
            action=action,
            form=scene.HtmlFormRef(binding.key),
        )

    def text(self, value: TextLike) -> scene.HtmlNode:
        return html_text(resolve_text(value, self.localization).nodes)

    def _resolved(self, value: TextLike) -> str:
        return resolve_text(value, self.localization).plain()

    def element(
        self,
        tag: scene.HtmlTag,
        *children: scene.HtmlNode,
        attributes: tuple[scene.HtmlAttribute, ...] = (),
        action: scene.HtmlActionRef | None = None,
        form: scene.HtmlFormRef | None = None,
    ) -> scene.HtmlElement:
        return scene.HtmlElement(tag, tuple(children), attributes, action=action, form=form)

    def _form_field[ContextT](
        self, form_key: str, spec: form_types.FormSpec[ContextT], field: form_types.FormField[Any, Any]
    ) -> scene.HtmlElement:
        control_id = f"squid-{form_key}-{field.key}"
        description_id = f"{control_id}-description"
        common = _attributes(
            (scene.HtmlAttributeName.ID, control_id),
            (scene.HtmlAttributeName.NAME, field.key),
            (scene.HtmlAttributeName.REQUIRED, field.required if field.required else None),
            (scene.HtmlAttributeName.ARIA_DESCRIBEDBY, description_id if field.description is not None else None),
        )
        prefill = spec.prefill_for(field)
        if isinstance(field, form_types.TextAreaField):
            control = self.element(
                scene.HtmlTag.TEXTAREA,
                scene.HtmlText(_scalar(prefill)),
                attributes=common
                + _attributes(
                    (scene.HtmlAttributeName.PLACEHOLDER, self._optional_text(field.placeholder)),
                    (scene.HtmlAttributeName.MINLENGTH, field.minimum),
                    (scene.HtmlAttributeName.MAXLENGTH, field.maximum),
                ),
                form=scene.HtmlFormRef(form_key, field.key),
            )
        elif isinstance(field, form_types.ChoiceField | form_types.MultiChoiceField | form_types.ScaleField):
            if isinstance(field, form_types.ScaleField):
                options = tuple((str(point), field.label_for(point)) for point in field.points)
                selected = {_scalar(prefill)}
                multiple = False
            else:
                options = tuple((option.key, option.label) for option in field.options)
                selected = set(prefill if isinstance(prefill, tuple) else (_scalar(prefill),))
                multiple = isinstance(field, form_types.MultiChoiceField)
            control = self.element(
                scene.HtmlTag.SELECT,
                *(
                    self.element(
                        scene.HtmlTag.OPTION,
                        self.text(label),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.VALUE, key),
                            (scene.HtmlAttributeName.SELECTED, True if key in selected else None),
                        ),
                    )
                    for key, label in options
                ),
                attributes=common + _attributes((scene.HtmlAttributeName.MULTIPLE, True if multiple else None)),
                form=scene.HtmlFormRef(form_key, field.key),
            )
        else:
            input_type = "text"
            extra: tuple[scene.HtmlAttribute, ...] = ()
            if isinstance(field, form_types.IntField):
                input_type = "number"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, field.minimum),
                    (scene.HtmlAttributeName.MAX, field.maximum),
                    (scene.HtmlAttributeName.STEP, 1),
                )
            elif isinstance(field, form_types.FloatField):
                input_type = "number"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, field.minimum),
                    (scene.HtmlAttributeName.MAX, field.maximum),
                    (scene.HtmlAttributeName.STEP, "any"),
                )
            elif isinstance(field, form_types.DateField):
                input_type = "date"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, None if field.minimum is None else field.minimum.isoformat()),
                    (scene.HtmlAttributeName.MAX, None if field.maximum is None else field.maximum.isoformat()),
                )
            elif isinstance(field, form_types.TimeField):
                input_type = "time"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, None if field.minimum is None else field.minimum.isoformat()),
                    (scene.HtmlAttributeName.MAX, None if field.maximum is None else field.maximum.isoformat()),
                )
            elif isinstance(field, form_types.DateTimeField | form_types.ZonedDateTimeField):
                input_type = "datetime-local"
                if isinstance(field, form_types.ZonedDateTimeField):
                    extra = _attributes((scene.HtmlAttributeName.TIMEZONE, field.timezone))
            elif isinstance(field, form_types.BoolField):
                input_type = "checkbox"
                extra = _attributes((scene.HtmlAttributeName.CHECKED, True if prefill else None))
            placeholder = getattr(field, "placeholder", None)
            control = self.element(
                scene.HtmlTag.INPUT,
                attributes=common
                + _attributes(
                    (scene.HtmlAttributeName.TYPE, input_type),
                    (
                        scene.HtmlAttributeName.VALUE,
                        None if isinstance(field, form_types.BoolField) else _scalar(prefill),
                    ),
                    (scene.HtmlAttributeName.PLACEHOLDER, self._optional_text(placeholder)),
                )
                + extra,
                form=scene.HtmlFormRef(form_key, field.key),
            )
        children: list[scene.HtmlNode] = [
            self.element(
                scene.HtmlTag.LABEL,
                self.text(cast(TextLike, field.label)),
                attributes=_attributes((scene.HtmlAttributeName.FOR, control_id)),
            ),
            control,
        ]
        if field.description is not None:
            children.append(
                self.element(
                    scene.HtmlTag.SMALL,
                    self.text(field.description),
                    attributes=_attributes((scene.HtmlAttributeName.ID, description_id)),
                )
            )
        return self.element(
            scene.HtmlTag.DIV, *children, attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-field"))
        )

    def _optional_text(self, value: TextLike | None) -> str | None:
        return None if value is None else self._resolved(value)

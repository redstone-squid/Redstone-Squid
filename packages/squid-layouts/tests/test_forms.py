"""Portable form schemas and descriptor compilation."""

from dataclasses import dataclass
from datetime import date
from typing import ClassVar

import pytest

import squid_layouts as sl
from squid_layouts.planning.limits import LIMITS
from squid_layouts.planning.target import TargetProfile
from squid_layouts.scene.model import SceneButton, SceneRow


class ProfileForm(sl.Form):
    title = "Edit profile"
    name = sl.TextField(minimum=2)
    age = sl.IntField(label="Age", minimum=13)
    public = sl.BoolField(label="Public", default=False)

    def __init__(self, **prefill: object) -> None:
        super().__init__(**prefill)
        self.validations = 0

    def validate(self):
        self.validations += 1
        return (sl.FormError("Name and age cannot match."),) if self.name == str(self.age) else ()


def test_descriptor_form_compiles_keys_labels_and_prefill() -> None:
    form = ProfileForm(name="Ada", age=36)

    spec = form.spec()

    assert spec.title == "Edit profile"
    assert [field.key for field in spec.fields] == ["name", "age", "public"]
    assert [field.label for field in spec.fields] == ["Name", "Age", "Public"]
    assert spec.prefill == {"name": "Ada", "age": 36, "public": False}


async def test_parse_errors_are_field_errors_and_gate_cross_field_validation() -> None:
    form = ProfileForm()
    spec = form.spec()

    failed = await spec.evaluate({"name": "A", "age": "young", "public": False})

    assert failed.errors == (
        sl.FieldError("name", "Enter at least 2 characters."),
        sl.FieldError("age", "Enter a whole number."),
    )
    assert form.validations == 0

    valid = await spec.evaluate({"name": "Ada", "age": "36", "public": True})
    assert valid.errors == ()
    assert valid.values == {"name": "Ada", "age": 36, "public": True}
    assert form.validations == 1


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        (sl.FloatField(key="value", label="Value"), "1.5", 1.5),
        (sl.DurationField(key="value", label="Value"), "2h", 7200),
        (sl.DateField(key="value", label="Value"), "2026-08-21", date(2026, 8, 21)),
        (
            sl.ChoiceField(
                key="value",
                label="Value",
                options=(sl.ChoiceOption("one", "One", 1), sl.ChoiceOption("two", "Two", 2)),
            ),
            "two",
            2,
        ),
    ],
)
async def test_typed_fields_parse_portable_values(field, raw, expected) -> None:
    result = await sl.FormSpec("Typed", (field,)).evaluate({"value": raw})

    assert result.errors == ()
    assert result.values["value"] == expected


async def _submitted(event: sl.SubmitEvent) -> None: ...


def test_form_trigger_plans_as_content_with_a_submission_binding() -> None:
    target = TargetProfile("html", 1, frozenset({"forms.inline"}), limits=LIMITS)
    spec = sl.FormSpec("Edit", (sl.TextField(key="name", label="Name"),))

    result = sl.plan(sl.form(spec, key="edit", label="Edit", on_submit=_submitted), target=target)

    row = result.scene.children[0]
    assert isinstance(row, SceneRow)
    assert isinstance(row.items[0], SceneButton)
    assert row.items[0].action == "edit"
    assert result.bindings["edit"].policy is sl.ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class NativeOnlyField(sl.ExtensionField[str]):
    capability: ClassVar[str] = "forms.native-only"

    def parse(self, raw: object) -> str | None:
        return None if raw is None else str(raw)


def test_extension_field_without_a_fallback_is_a_planning_error() -> None:
    target = TargetProfile("html", 1, frozenset({"forms.inline"}), limits=LIMITS)
    spec = sl.FormSpec("Native", (NativeOnlyField(key="native", label="Native"),))

    with pytest.raises(sl.LayoutInvariantError, match=r"forms\.native-only"):
        sl.plan(sl.form(spec, key="native", on_submit=_submitted), target=target)


def test_extension_field_uses_its_portable_fallback() -> None:
    target = TargetProfile("html", 1, frozenset({"forms.inline"}), limits=LIMITS)
    spec = sl.FormSpec(
        "Native",
        (
            NativeOnlyField(
                key="native",
                label="Native",
                fallback=sl.TextField(label="Native id"),
            ),
        ),
    )

    result = sl.plan(sl.form(spec, key="native", on_submit=_submitted), target=target)

    assert isinstance(result.scene.children[0], SceneRow)

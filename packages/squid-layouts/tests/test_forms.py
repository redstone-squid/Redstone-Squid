"""Portable form schemas and descriptor compilation."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import ClassVar
from zoneinfo import ZoneInfo

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


def _multi_choice(*, required: bool = True, minimum: int = 0, maximum: int | None = None) -> sl.MultiChoiceField[int]:
    return sl.MultiChoiceField(
        key="values",
        label="Values",
        options=(
            sl.ChoiceOption("one", "One", 1),
            sl.ChoiceOption("two", "Two", 2),
            sl.ChoiceOption("three", "Three", 3),
        ),
        required=required,
        minimum=minimum,
        maximum=maximum,
    )


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


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        (sl.TimeField(key="value", label="Value"), "14:30", time(14, 30)),
        (
            sl.DateTimeField(key="value", label="Value"),
            "2026-08-22 14:30",
            datetime(2026, 8, 22, 14, 30, tzinfo=UTC),
        ),
        (
            sl.DateTimeField(key="value", label="Value", timezone=timezone(timedelta(hours=2))),
            "2026-08-22T14:30",
            datetime(2026, 8, 22, 14, 30, tzinfo=timezone(timedelta(hours=2))),
        ),
    ],
)
async def test_temporal_fields_parse_portable_values(field, raw, expected) -> None:
    result = await sl.FormSpec("Temporal", (field,)).evaluate({"value": raw})

    assert result.errors == ()
    assert result.values["value"] == expected


@pytest.mark.parametrize(
    ("field", "raw", "message"),
    [
        (sl.TimeField(key="value", label="Value"), "later", "HH:MM"),
        (sl.TimeField(key="value", label="Value", minimum=time(9)), "08:59", "at or after"),
        (sl.TimeField(key="value", label="Value", maximum=time(17)), "17:01", "at or before"),
        (sl.DateTimeField(key="value", label="Value"), "tomorrow", "YYYY-MM-DD HH:MM"),
        (
            sl.DateTimeField(key="value", label="Value", minimum=datetime(2026, 8, 22, tzinfo=UTC)),
            "2026-08-21 23:59Z",
            "on or after",
        ),
        (
            sl.DateTimeField(key="value", label="Value", maximum=datetime(2026, 8, 22, tzinfo=UTC)),
            "2026-08-22 00:01Z",
            "on or before",
        ),
    ],
)
async def test_temporal_fields_report_parse_and_bound_errors(field, raw, message) -> None:
    result = await sl.FormSpec("Temporal", (field,)).evaluate({"value": raw})

    assert message in str(result.errors[0].message)


async def test_datetime_field_rejects_ambiguous_local_time_by_default() -> None:
    field = sl.DateTimeField(key="value", label="Value", timezone=ZoneInfo("America/New_York"))

    result = await sl.FormSpec("Temporal", (field,)).evaluate({"value": "2026-11-01 01:30"})

    assert "occurs twice" in str(result.errors[0].message)


def test_datetime_field_interprets_ordinary_local_time_in_iana_zone() -> None:
    zone = ZoneInfo("America/New_York")

    value = sl.DateTimeField(timezone=zone).parse("2026-08-22 14:30")

    assert value == datetime(2026, 8, 22, 14, 30, tzinfo=zone)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (sl.AmbiguousTimePolicy.EARLIER, datetime(2026, 11, 1, 5, 30, tzinfo=UTC)),
        (sl.AmbiguousTimePolicy.LATER, datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
    ],
)
def test_datetime_field_resolves_ambiguous_local_time_by_instant(policy, expected) -> None:
    field = sl.DateTimeField(timezone=ZoneInfo("America/New_York"), ambiguous=policy)

    value = field.parse("2026-11-01 01:30")

    assert value is not None
    assert value.astimezone(UTC) == expected


async def test_datetime_field_rejects_nonexistent_local_time_by_default() -> None:
    field = sl.DateTimeField(key="value", label="Value", timezone=ZoneInfo("America/New_York"))

    result = await sl.FormSpec("Temporal", (field,)).evaluate({"value": "2026-03-08 02:30"})

    assert "does not exist" in str(result.errors[0].message)


@pytest.mark.parametrize(
    ("zone", "raw", "expected"),
    [
        ("America/New_York", "2026-03-08 02:30", "2026-03-08T03:30:00-04:00"),
        ("Australia/Lord_Howe", "2026-10-04 02:15", "2026-10-04T02:45:00+11:00"),
    ],
)
def test_datetime_field_shifts_nonexistent_local_time_by_the_gap(zone, raw, expected) -> None:
    field = sl.DateTimeField(
        timezone=ZoneInfo(zone),
        nonexistent=sl.NonexistentTimePolicy.SHIFT_FORWARD,
    )

    value = field.parse(raw)

    assert value is not None
    assert value.isoformat() == expected


def test_datetime_field_keeps_explicit_offset_input_exact() -> None:
    field = sl.DateTimeField(timezone=ZoneInfo("America/New_York"))

    value = field.parse("2026-11-01 01:30-05:00")

    assert value is not None
    assert value.isoformat() == "2026-11-01T01:30:00-05:00"
    assert value.astimezone(UTC) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_datetime_field_applies_bounds_to_ambiguous_instants() -> None:
    zone = ZoneInfo("America/New_York")
    minimum = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    field = sl.DateTimeField(
        timezone=zone,
        minimum=minimum,
        ambiguous=sl.AmbiguousTimePolicy.EARLIER,
    )

    with pytest.raises(sl.FormValueError, match="on or after"):
        field.parse("2026-11-01 01:30")


def test_zoned_datetime_field_returns_instant_and_configured_zone() -> None:
    field = sl.ZonedDateTimeField(timezone="America/New_York")

    value = field.parse("2026-08-22 10:30")

    assert value == sl.ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "America/New_York")


async def test_zoned_datetime_field_rejects_ambiguous_and_nonexistent_input_by_default() -> None:
    field = sl.ZonedDateTimeField(key="value", label="Value", timezone="America/New_York")
    spec = sl.FormSpec("Zoned", (field,))

    ambiguous = await spec.evaluate({"value": "2026-11-01 01:30"})
    nonexistent = await spec.evaluate({"value": "2026-03-08 02:30"})

    assert "occurs twice" in str(ambiguous.errors[0].message)
    assert "does not exist" in str(nonexistent.errors[0].message)


def test_zoned_datetime_field_resolves_local_time_with_policies() -> None:
    overlap = sl.ZonedDateTimeField(
        timezone="America/New_York",
        ambiguous=sl.AmbiguousTimePolicy.LATER,
    ).parse("2026-11-01 01:30")
    gap = sl.ZonedDateTimeField(
        timezone="America/New_York",
        nonexistent=sl.NonexistentTimePolicy.SHIFT_FORWARD,
    ).parse("2026-03-08 02:30")

    assert overlap is not None
    assert overlap.isoformat() == "2026-11-01 01:30:00-05:00[America/New_York]"
    assert gap is not None
    assert gap.isoformat() == "2026-03-08 03:30:00-04:00[America/New_York]"


def test_zoned_datetime_field_uses_valid_offset_to_select_overlap() -> None:
    field = sl.ZonedDateTimeField(timezone="America/New_York")

    earlier = field.parse("2026-11-01 01:30-04:00")
    later = field.parse("2026-11-01 01:30-05:00")

    assert earlier is not None
    assert earlier.instant == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert later is not None
    assert later.instant == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_zoned_datetime_field_rejects_offset_conflicts() -> None:
    field = sl.ZonedDateTimeField(timezone="America/New_York")

    with pytest.raises(sl.FormValueError, match="offset does not match"):
        field.parse("2026-08-22 10:30-05:00")
    with pytest.raises(sl.FormValueError, match="offset does not match"):
        field.parse("2026-03-08 02:30-05:00")


def test_zoned_datetime_field_prefill_round_trips_exact_overlap() -> None:
    field = sl.ZonedDateTimeField(timezone="America/New_York")
    value = sl.ZonedDateTime(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "America/New_York")

    prefill = field.format_prefill(value)

    assert prefill == "2026-11-01T01:30:00-05:00"
    assert field.parse(prefill) == value


def test_zoned_datetime_field_applies_instant_bounds() -> None:
    field = sl.ZonedDateTimeField(
        timezone="America/New_York",
        minimum=datetime(2026, 11, 1, 6, tzinfo=UTC),
    )

    with pytest.raises(sl.FormValueError, match="on or after"):
        field.parse("2026-11-01 01:30-04:00")


def test_temporal_field_prefill_uses_isoformat() -> None:
    instant = datetime(2026, 8, 22, 14, 30, tzinfo=UTC)

    assert sl.TimeField().format_prefill(time(14, 30)) == "14:30:00"
    assert sl.DateTimeField().format_prefill(instant) == "2026-08-22T14:30:00+00:00"


async def test_multi_choice_orders_values_by_declaration_and_accepts_one_key() -> None:
    field = _multi_choice(required=False)

    many = await sl.FormSpec("Many", (field,)).evaluate({"values": ["three", "one"]})
    one = await sl.FormSpec("One", (field,)).evaluate({"values": "two"})

    assert many.values["values"] == (1, 3)
    assert one.values["values"] == (2,)


@pytest.mark.parametrize(
    ("field", "raw", "message"),
    [
        (_multi_choice(required=False), ["missing"], "available options"),
        (_multi_choice(required=False, minimum=2), ["one"], "at least 2"),
        (_multi_choice(required=False, maximum=1), ["one", "two"], "no more than 1"),
        (_multi_choice(), [], "required"),
    ],
)
async def test_multi_choice_reports_unknown_cardinality_and_required_errors(field, raw, message) -> None:
    result = await sl.FormSpec("Many", (field,)).evaluate({"values": raw})

    assert len(result.errors) == 1
    assert message in str(result.errors[0].message)


def test_multi_choice_prefill_round_trips_typed_values_and_keys() -> None:
    field = _multi_choice(required=False)

    assert field.format_prefill((3, 1)) == ("one", "three")
    assert field.format_prefill(["two", "one"]) == ("one", "two")


def test_multi_choice_rejects_duplicate_option_keys() -> None:
    with pytest.raises(ValueError, match="option keys must be unique"):
        sl.MultiChoiceField(options=(sl.ChoiceOption("same", "One", 1), sl.ChoiceOption("same", "Two", 2)))


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


@dataclass(frozen=True, slots=True)
class BrokenField(sl.FormField[str]):
    """A field with a bug in its parser, as opposed to input a reader can correct."""

    def parse(self, raw: object) -> str | None:
        return raw.no_such_attribute  # type: ignore[attr-defined]


async def test_a_bug_in_a_field_propagates_instead_of_becoming_a_field_error() -> None:
    spec = sl.FormSpec("Broken", (BrokenField(key="broken", label="Broken"),))

    with pytest.raises(AttributeError):
        await spec.evaluate({"broken": "value"})


@dataclass(frozen=True, slots=True)
class CorrectableField(sl.FormField[str]):
    def parse(self, raw: object) -> str | None:
        if raw != "good":
            message = "Type 'good'."
            raise sl.FormValueError(message)
        return "good"


async def test_form_value_error_is_still_reported_to_the_reader() -> None:
    spec = sl.FormSpec("Correctable", (CorrectableField(key="pick", label="Pick"),))

    evaluated = await spec.evaluate({"pick": "bad"})

    assert evaluated.errors == (sl.FieldError("pick", "Type 'good'."),)


async def test_a_custom_duration_parser_reports_bad_input_as_a_field_error() -> None:
    def parse(value: str) -> int:
        message = "Durations look like 30m."
        raise ValueError(message)

    spec = sl.FormSpec("Duration", (sl.DurationField(key="for", label="For", parser=parse),))

    evaluated = await spec.evaluate({"for": "banana"})

    assert evaluated.errors == (sl.FieldError("for", "Durations look like 30m."),)


def test_scale_field_parses_a_picked_point_and_a_typed_one() -> None:
    field = sl.ScaleField(key="score", label="Score")

    assert field.parse("3") == 3
    assert field.parse(5) == 5
    assert [option for option in field.points] == [1, 2, 3, 4, 5]


def test_scale_field_rejects_values_outside_its_span() -> None:
    field = sl.ScaleField(key="score", label="Score", minimum=1, maximum=5)

    with pytest.raises(sl.FormValueError, match="from 1 to 5"):
        field.parse("6")
    with pytest.raises(sl.FormValueError, match="from 1 to 5"):
        field.parse("0")
    with pytest.raises(sl.FormValueError, match="whole number"):
        field.parse("great")


def test_scale_field_labels_named_points_and_numbers_the_rest() -> None:
    field = sl.ScaleField(key="score", labels={1: "Poor", 5: "Excellent"})

    assert field.label_for(1) == "Poor"
    assert field.label_for(3) == "3"
    assert field.label_for(5) == "Excellent"


def test_scale_field_prefill_is_the_option_key() -> None:
    field = sl.ScaleField(key="score")

    assert field.format_prefill(4) == "4"
    assert field.format_prefill(None) is None


def test_scale_field_needs_a_span_to_pick_from() -> None:
    with pytest.raises(ValueError, match="maximum greater than minimum"):
        sl.ScaleField(key="score", minimum=3, maximum=3)


def test_scale_is_the_short_alias() -> None:
    assert sl.Scale is sl.ScaleField

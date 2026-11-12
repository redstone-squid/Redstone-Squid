"""Form planning rejects impossible presentations before constructing Discord controls."""

from dataclasses import replace
from unittest.mock import patch

import discord
import pytest

from squid_ui.errors import LayoutInvariantError, LimitViolationError
from squid_ui.forms import ChoiceField, ChoiceOption, FormSpec, TextField
from squid_ui.planning.limits import COMPONENT_LIMITS
from squid_ui_discord.form_planning import ChoiceControl, FieldPlan, TextControl, plan_form
from squid_ui_discord.modal import render_form_modal
from squid_ui_discord.testing import interaction_harness


@pytest.mark.parametrize("count,kind", [(1, "select"), (2, "radio"), (10, "radio"), (11, "select"), (25, "select")])
def test_planning_chooses_controls_without_allocating_sdk_items(count: int, kind: str) -> None:
    spec = FormSpec(
        "Choose",
        (
            ChoiceField(
                key="choice",
                label="Choice",
                options=tuple(ChoiceOption(str(index), f"Choice {index}", index) for index in range(count)),
            ),
        ),
    )
    with (
        patch.object(discord.ui, "RadioGroup", side_effect=AssertionError("allocated during planning")),
        patch.object(discord.ui, "Select", side_effect=AssertionError("allocated during planning")),
    ):
        plan = plan_form(spec)
    field = plan.items[0]
    assert isinstance(field, FieldPlan)
    assert isinstance(field.control, ChoiceControl)
    assert field.control.kind == kind


def test_text_repairs_are_part_of_plan_and_strict_mode_rejects_them() -> None:
    spec = FormSpec("Long title", (TextField(key="name", label="Long label", default="abcdef", maximum=3),))
    limits = replace(COMPONENT_LIMITS, modal_title=5, label_text=5)
    plan = plan_form(spec, limits=limits)
    assert plan.title == "Long…"
    field = plan.items[0]
    assert isinstance(field, FieldPlan)
    assert field.label == "Long…"
    assert isinstance(field.control, TextControl)
    assert field.control.default == "ab…"
    assert len(plan.interventions) == 3
    with pytest.raises(LimitViolationError):
        plan_form(spec, limits=limits, strict=True)


def test_option_keys_are_rejected_instead_of_silently_truncated() -> None:
    spec = FormSpec("Choose", (ChoiceField(key="choice", label="Choice", options=(ChoiceOption("long-key", "A", 1),)),))
    with pytest.raises(LayoutInvariantError, match="option key"):
        plan_form(spec, limits=replace(COMPONENT_LIMITS, option_value=4))


async def test_rendering_plan_creates_independent_controls_and_preserves_submission_keys() -> None:
    spec = FormSpec("Name", (TextField(key="name", label="Name", default="initial"),))
    plan = plan_form(spec)
    received: list[dict[str, object]] = []

    async def capture(_interaction: discord.Interaction, values: dict[str, object]) -> None:
        received.append(values)

    first = render_form_modal(plan, on_submit=capture)
    second = render_form_modal(plan, on_submit=capture)
    first_label, second_label = first.children[0], second.children[0]
    assert isinstance(first_label, discord.ui.Label)
    assert isinstance(second_label, discord.ui.Label)
    first_input, second_input = first_label.component, second_label.component
    assert isinstance(first_input, discord.ui.TextInput)
    assert isinstance(second_input, discord.ui.TextInput)
    assert first_input is not second_input
    first_input._value = "changed"
    await first.on_submit(interaction_harness())
    assert received == [{"name": "changed"}]
    assert second_input.default == "initial"
    assert plan_form(spec) == plan

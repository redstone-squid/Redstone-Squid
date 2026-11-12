"""Form planning rejects impossible presentations before constructing Discord controls."""

from dataclasses import replace
from typing import override
from unittest.mock import patch

import discord
import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError, LimitViolationError
from squid_ui.forms import ChoiceField, ChoiceOption, FormSpec, ScaleField, TextField
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.limits import COMPONENT_LIMITS
from squid_ui.planning.planner import plan
from squid_ui.planning.target import AnyTarget
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.form_fields import CheckboxGroupField
from squid_ui_discord.form_planning import ChoiceControl, DiscordFormPlan, FieldPlan, TextControl, plan_form
from squid_ui_discord.modal import render_form_modal
from squid_ui_discord.target import DISCORD_V1_DPY27, DISCORD_V2_DPY27
from squid_ui_discord.testing import commit_render, interaction_harness


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


async def _submit(_event: sl.SubmitEvent) -> None:
    pass


@pytest.mark.parametrize("target", [DISCORD_V2_DPY27, DISCORD_V1_DPY27])
def test_declared_forms_are_validated_during_message_planning(target: AnyTarget) -> None:
    spec = FormSpec(
        "Choose",
        (
            ChoiceField(
                key="choice",
                label="Choice",
                options=tuple(ChoiceOption(str(index), str(index), index) for index in range(26)),
            ),
        ),
    )
    with pytest.raises(LayoutInvariantError, match=r"choice.*1-25"):
        plan(sl.form("Choose", spec, key="choose", on_submit=_submit), target=target)


class _Panel(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, spec: FormSpec) -> None:
        self.spec = spec

    @override
    def render(self) -> sl.LayoutNode:
        return sl.form("Open", self.spec, key="open", on_submit=_submit)


async def test_declared_form_opens_the_prepared_plan_without_replanning() -> None:
    root = MessageRoot(
        _Panel(FormSpec("Name", (TextField(key="name", label="Name", default="Ada"),))), access=Everyone(), timeout=None
    )
    commit_render(root)
    assert root.plan is not None
    prepared = root.plan.form_plans["open"]
    assert isinstance(prepared, DiscordFormPlan)
    interaction = interaction_harness()
    with patch("squid_ui_discord.actions.build_form_modal", side_effect=AssertionError("replanned declared form")):
        await root.dispatch("open", interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.TextInput)
    assert label.component.default == "Ada"


async def test_dynamic_form_uses_current_values_instead_of_a_declared_plan_with_the_same_key() -> None:
    spec = FormSpec("Name", (TextField(key="name", label="Name", default="Ada"),))
    root = MessageRoot(_Panel(spec), access=Everyone(), timeout=None)
    commit_render(root)
    interaction = interaction_harness()
    await ActionResponder(interaction, root).present_form(
        spec.with_prefill({"name": "Grace"}), key="open", on_submit=_submit
    )
    modal = interaction.response.send_modal.await_args.args[0]
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.TextInput)
    assert label.component.default == "Grace"


def test_cached_message_replans_forms_when_defaults_change() -> None:
    cache: PlanCache[scene.ComponentsV2] = PlanCache()
    spec = FormSpec("Name", (TextField(key="name", label="Name", default="Ada"),))
    first = plan(sl.form("Open", spec, key="open", on_submit=_submit), target=DISCORD_V2_DPY27, cache=cache)
    second = plan(
        sl.form("Open", spec.with_prefill({"name": "Grace"}), key="open", on_submit=_submit),
        target=DISCORD_V2_DPY27,
        cache=cache,
    )
    prepared_first, prepared_second = first.form_plans["open"], second.form_plans["open"]
    assert isinstance(prepared_first, DiscordFormPlan)
    assert isinstance(prepared_second, DiscordFormPlan)
    field_first, field_second = prepared_first.items[0], prepared_second.items[0]
    assert isinstance(field_first, FieldPlan) and isinstance(field_second, FieldPlan)
    assert isinstance(field_first.control, TextControl) and isinstance(field_second.control, TextControl)
    assert field_first.control.default == "Ada"
    assert field_second.control.default == "Grace"


async def test_dynamic_invalid_form_is_rejected_before_sending_a_modal() -> None:
    spec = FormSpec("Choose", (ChoiceField(key="choice", label="Choice", options=()),))
    root = MessageRoot(
        _Panel(FormSpec("Name", (TextField(key="name", label="Name"),))), access=Everyone(), timeout=None
    )
    commit_render(root)
    interaction = interaction_harness()
    with pytest.raises(LayoutInvariantError, match="1-25"):
        await ActionResponder(interaction, root).present_form(spec, on_submit=_submit)
    interaction.response.send_modal.assert_not_awaited()


def test_declared_form_respects_target_text_limits_and_strict_mode() -> None:
    spec = FormSpec("Long title", (TextField(key="name", label="Name"),))
    target = replace(
        DISCORD_V2_DPY27, limits=replace(DISCORD_V2_DPY27.limits, components=replace(COMPONENT_LIMITS, modal_title=5))
    )
    document = sl.form("Open", spec, key="open", on_submit=_submit)
    prepared = plan(document, target=target).form_plans["open"]
    assert isinstance(prepared, DiscordFormPlan)
    assert prepared.title == "Long…"
    with pytest.raises(LimitViolationError):
        plan(document, target=target, strict=True)


def test_scale_text_fallback_is_conformed_before_rendering() -> None:
    spec = FormSpec("Score", (ScaleField(key="score", label="Score", minimum=0, maximum=100, default=42),))
    limits = replace(COMPONENT_LIMITS, text_input_placeholder=3, text_input_value=1)
    prepared = plan_form(spec, limits=limits)
    field = prepared.items[0]
    assert isinstance(field, FieldPlan)
    assert isinstance(field.control, TextControl)
    assert field.control.placeholder == "0\N{EN DASH}…"
    assert field.control.default == "…"
    with pytest.raises(LimitViolationError):
        plan_form(spec, limits=limits, strict=True)


def test_native_checkbox_option_limit_belongs_to_planning() -> None:
    field = CheckboxGroupField(
        key="checks", label="Checks", options=tuple(ChoiceOption(str(index), str(index), index) for index in range(11))
    )
    assert field.parse(["10"]) == (10,)
    with pytest.raises(LayoutInvariantError, match="1-10"):
        plan_form(FormSpec("Checks", (field,)))

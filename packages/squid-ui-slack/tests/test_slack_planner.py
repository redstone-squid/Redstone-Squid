"""Slack message, Home, and modal planning behavior."""

from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

import squid_ui as sl
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.content.entity import ConversationType, EntityKind, EntityRef, EntityType
from squid_ui.ext.slack.target_types import SlackAdapter, SlackMessageTarget
from squid_ui.interactions import ActionEvent, SubmitEvent, forms
from squid_ui.planning import PlanCache, PlanningSnapshot, ResourceCost
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.planner import plan_request
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import Axis
from squid_ui.planning.result import PlanReuse, PlanSeverity
from squid_ui.runtime import PresentationState

ADAPTER = AdapterProfile(SlackAdapter, "tests.slack", ">=3.43,<4")
MESSAGE = squid_ui.ext.slack.message_target(adapter=ADAPTER)
MODAL = squid_ui.ext.slack.modal_target(adapter=ADAPTER)
HOME = squid_ui.ext.slack.home_target(adapter=ADAPTER)


async def _pressed(_event: ActionEvent) -> None: ...


async def _submitted(_event: SubmitEvent) -> None: ...


async def _entities_changed(_event: sl.EntityEvent) -> None: ...


def test_message_compiles_text_actions_dates_and_accessible_fallback() -> None:
    instant = datetime(2026, 8, 29, 12, 30, tzinfo=UTC)
    result = sl.planning.plan(
        sl.stack(
            sl.heading("Build **review**"),
            sl.paragraph("See [docs](https://example.invalid/docs) & stay safe"),
            sl.timestamp(instant),
            sl.action_controls(
                sl.action_control("Approve", _pressed, key="approve", tone=sl.Tone.SUCCESS),
                sl.link("Source", "https://example.invalid/source", key="source"),
                key="actions",
            ),
        ),
        target=MESSAGE,
    )

    assert isinstance(result.scene.body, scene.SlackMessage)
    assert "Build **review**" in result.scene.body.text
    assert any(isinstance(block, scene.SlackHeader) for block in result.scene.body.blocks)
    assert any(
        isinstance(block, scene.SlackSection) and block.text is not None and "<!date^" in block.text.content
        for block in result.scene.body.blocks
    )
    actions = next(block for block in result.scene.body.blocks if isinstance(block, scene.SlackActions))
    assert [button.style for button in actions.elements if isinstance(button, scene.SlackButton)] == [
        scene.SlackButtonStyle.PRIMARY,
        scene.SlackButtonStyle.DEFAULT,
    ]
    assert set(result.bindings) == {"approve"}


def test_cards_are_planned_to_the_sdk_card_text_limits() -> None:
    result = sl.planning.plan(
        sl.truncate(sl.article(sl.heading("T" * 200), sl.paragraph("B" * 300))),
        target=HOME,
    )

    header = next(block for block in result.scene.body.blocks if isinstance(block, scene.SlackHeader))
    assert len(header.text.content) == 150
    assert any(
        isinstance(block, scene.SlackSection) and block.text is not None and block.text.content == "B" * 300
        for block in result.scene.body.blocks
    )


def test_message_and_home_use_native_slack_entity_selectors() -> None:
    user = sl.entities(
        key="reviewer",
        entity_type=EntityType.USER,
        selection=sl.controlled((EntityRef(EntityKind.USER, "U123"),), _entities_changed),
    )
    conversation = sl.entities(
        key="conversation",
        entity_type=EntityType.CONVERSATION,
        conversation_types=(ConversationType.WORKSPACE_PUBLIC, ConversationType.WORKSPACE_PRIVATE),
    )

    for target in (cast(Any, MESSAGE), cast(Any, HOME)):
        result = sl.planning.plan(sl.stack(user, conversation), target=target)
        selects = tuple(
            element
            for block in result.scene.body.blocks
            if isinstance(block, scene.SlackActions)
            for element in block.elements
            if isinstance(element, scene.SlackSelect)
        )

        assert [select.kind for select in selects] == [
            scene.SlackSelectKind.USERS,
            scene.SlackSelectKind.CONVERSATIONS,
        ]
        assert selects[0].initial_values == ("U123",)
        assert selects[1].conversation_types == (
            ConversationType.WORKSPACE_PUBLIC,
            ConversationType.WORKSPACE_PRIVATE,
        )


def test_unsupported_native_entity_family_requires_an_enumerated_fallback() -> None:
    with pytest.raises(JointUnsupportedError):
        sl.planning.plan(sl.entities(key="role", entity_type=EntityType.ROLE), target=MESSAGE)

    result = sl.planning.plan(
        sl.entities(
            sl.entity_choice(EntityRef(EntityKind.ROLE, "R123"), "Reviewer"),
            key="role",
            entity_type=EntityType.ROLE,
        ),
        target=MESSAGE,
    )

    actions = next(block for block in result.scene.body.blocks if isinstance(block, scene.SlackActions))
    assert isinstance(actions.elements[0], scene.SlackSelect)
    assert actions.elements[0].kind is scene.SlackSelectKind.STATIC


@pytest.mark.parametrize("shared", [False, True])
def test_message_form_is_a_modal_trigger_button_with_form_binding(shared: bool) -> None:
    spec = forms.FormSpec("Edit build", (forms.TextField("Name", "name"),))
    result = sl.planning.plan(
        sl.form("Edit", spec, key="edit", on_submit=_submitted),
        target=MESSAGE,
        composition=sl.planning.CompositionOptions(automatic=True) if shared else None,
    )

    assert len(result.scene.body.blocks) == 1
    assert isinstance(result.scene.body.blocks[0], scene.SlackActions)
    assert set(result.bindings) == {"edit"}
    assert set(result.form_bindings) == {"edit"}


@pytest.mark.parametrize("shared", [False, True])
def test_modal_requires_one_top_level_form_and_maps_portable_fields(shared: bool) -> None:
    spec = forms.FormSpec(
        "Edit build",
        (
            forms.FormText("All fields are reviewed."),
            forms.TextField("Name", "name", maximum=80),
            forms.TextAreaField("Notes", "notes"),
            forms.IntField("Ticks", "ticks", minimum=1, maximum=20),
            forms.DateField("Built", "built", default=date(2026, 8, 29)),
            forms.BoolField("Published", "published", default=True),
            forms.ChoiceField(
                "State",
                "state",
                options=(
                    forms.ChoiceOption("draft", "Draft", "draft"),
                    forms.ChoiceOption("ready", "Ready", "ready"),
                ),
            ),
        ),
    )
    result = sl.planning.plan(
        sl.Document(
            (
                sl.paragraph("Review this build."),
                sl.form("Save", spec, key="edit-build", on_submit=_submitted),
            )
        ),
        target=MODAL,
        composition=sl.planning.CompositionOptions(automatic=True) if shared else None,
    )

    body = result.scene.body
    assert isinstance(body, scene.SlackModalView)
    assert body.callback_id == "edit-build"
    assert body.title.content == "Edit build"
    assert body.submit.content == "Save"
    assert body.close.content == "Cancel"
    inputs = tuple(block for block in body.blocks if isinstance(block, scene.SlackInput))
    assert [type(block.element) for block in inputs] == [
        scene.SlackTextInput,
        scene.SlackTextInput,
        scene.SlackNumberInput,
        scene.SlackDatePicker,
        scene.SlackCheckboxes,
        scene.SlackRadioButtons,
    ]
    assert set(result.form_bindings) == {"edit-build"}
    assert not result.bindings


@pytest.mark.parametrize(
    "document",
    [
        sl.paragraph("No form"),
        sl.stack(
            sl.form(
                "Nested",
                forms.FormSpec("Nested", (forms.TextField("Name", "name"),)),
                key="nested",
                on_submit=_submitted,
            )
        ),
        sl.Document(
            (
                sl.form(
                    "One",
                    forms.FormSpec("One", (forms.TextField("Name", "name"),)),
                    key="one",
                    on_submit=_submitted,
                ),
                sl.form(
                    "Two",
                    forms.FormSpec("Two", (forms.TextField("Name", "name"),)),
                    key="two",
                    on_submit=_submitted,
                ),
            )
        ),
    ],
)
def test_modal_rejects_missing_nested_and_multiple_forms(document) -> None:
    with pytest.raises(sl.LayoutInvariantError, match=r"top-level sl\.form"):
        sl.planning.plan(document, target=MODAL)


def test_block_reservation_and_strict_degradation() -> None:
    document = sl.stack(*(sl.optional(sl.paragraph(f"Block {index}")) for index in range(3)))
    result = sl.planning.plan(document, target=MESSAGE, reservation=ResourceCost({Axis.BLOCKS: 49}))

    assert len(result.scene.body.blocks) == 1
    assert any(event.severity is PlanSeverity.DEGRADATION for event in result.report.events)
    with pytest.raises(JointInfeasibleError):
        sl.planning.plan(
            document,
            target=MESSAGE,
            reservation=ResourceCost({Axis.BLOCKS: 49}),
            strict=True,
        )


def test_slack_planner_reuses_exact_and_structural_caches() -> None:
    document = sl.paragraph("Cached")
    session = PresentationState()
    cache: PlanCache = PlanCache()
    snapshot: PlanningSnapshot[scene.SlackMessage] = PlanningSnapshot()

    first = sl.planning.plan(document, target=MESSAGE, session=session, cache=cache, snapshot=snapshot)
    exact = sl.planning.plan(document, target=MESSAGE, session=session, cache=cache, snapshot=snapshot)
    structural = sl.planning.plan(sl.paragraph("Cached"), target=MESSAGE, session=session, cache=cache)

    assert first.metrics.reuse is PlanReuse.MISS
    assert exact.metrics.reuse is PlanReuse.EXACT
    assert structural.metrics.partition_hint


def test_literal_paragraph_does_not_become_slack_mrkdwn() -> None:
    result = sl.planning.plan(sl.paragraph("*literal* <@123>"), target=MESSAGE)
    section = result.scene.body.blocks[0]

    assert isinstance(section, scene.SlackSection)
    assert section.text is not None
    assert section.text.kind is scene.SlackTextKind.PLAIN
    assert section.text.content == "*literal* <@123>"


def test_slack_lowers_translated_styles_from_structure() -> None:
    name = "Ada <@123>"
    value = sl.md_tr(t"**Hello** {name}", key="hello")
    result = sl.planning.plan(sl.paragraph(value), target=MESSAGE)
    section = result.scene.body.blocks[0]
    assert isinstance(section, scene.SlackSection)
    assert section.text is not None
    assert section.text.content == "*Hello* Ada &lt;@123&gt;"


def test_slack_keeps_literal_markdown_punctuation_literal() -> None:
    name = "*Ada*"
    value = sl.md_tr(t"Hello **{name}**", key="hello")
    result = sl.planning.plan(sl.fallback(sl.paragraph(value), sl.paragraph("Hello *Ada*")), target=MESSAGE)
    section = result.scene.body.blocks[0]
    assert isinstance(section, scene.SlackSection)
    assert section.text is not None
    assert section.text.kind is scene.SlackTextKind.PLAIN
    assert section.text.content == "Hello *Ada*"
    assert any(event.code == "composition.effect" for event in result.report.events)


@pytest.mark.parametrize("base", list(sl.Tone))
def test_custom_roles_preserve_builtin_target_behavior(base: sl.Tone) -> None:
    def document(tone: sl.Tone | sl.Role) -> sl.LayoutNode:
        return sl.group(
            sl.aside("Aside", tone=tone),
            sl.status("Status", tone=tone),
            sl.action_controls(sl.action_control("Go", _pressed, key="go", tone=tone), key="actions"),
        )

    builtin = sl.planning.plan(document(base), target=MESSAGE)
    custom = sl.planning.plan(document(sl.Role("app.custom", base)), target=MESSAGE)
    assert custom.scene == builtin.scene


def test_shared_slack_cache_rebuilds_handlers_and_preserves_document_assets() -> None:
    async def replacement(_event: ActionEvent) -> None:
        pass

    asset = sl.document.Asset("report", "report.txt", "text/plain", sl.document.InlineAsset(b"report"))
    cache = PlanCache()
    request = PlanRequest(target=MESSAGE)
    first = plan_request(
        sl.Document[SlackMessageTarget](
            (sl.action_controls(sl.action_control("Run", _pressed, key="run"), key="actions"),), assets=(asset,)
        ),
        request,
        cache=cache,
    )
    second = plan_request(
        sl.Document[SlackMessageTarget](
            (sl.action_controls(sl.action_control("Run", replacement, key="run"), key="actions"),), assets=(asset,)
        ),
        request,
        cache=cache,
    )
    assert second.metrics.partition_hint
    assert second.scene == first.scene
    assert second.report == first.report
    assert second.bindings["run"].handler is replacement
    assert first.bindings["run"].handler is _pressed
    assert second.resources == {"asset:report": asset}
    assert second.scene.assets == (scene.Asset(key="report", name="report.txt", media_type="text/plain"),)
    assert second.allocation is not first.allocation


def test_shared_slack_preserves_authored_budget_diagnostics_and_strict_failure() -> None:
    document = sl.presentations.budget(
        sl.stack(sl.paragraph("keep"), sl.optional(sl.paragraph("omit"))),
        limits={Axis.BLOCKS: 1},
    )
    result = plan_request(document, PlanRequest(target=MESSAGE))
    assert result.scene.body.text == "keep"
    assert any(event.code == "composition.effect" for event in result.report.events)
    with pytest.raises(JointInfeasibleError):
        plan_request(document, PlanRequest(target=MESSAGE, strict=True))


def test_shared_slack_only_retains_actions_on_the_selected_page() -> None:
    document = sl.region(
        sl.action_controls(sl.action_control("AAAA", _pressed, key="first"), key="first-actions"),
        sl.action_controls(sl.action_control("BBBB", _pressed, key="second"), key="second-actions"),
        key="manual",
        title="Actions",
    )
    session = PresentationState()
    region = sl.planning.OccurrenceId(("root", "manual"))
    result = plan_request(
        document,
        PlanRequest(
            target=MESSAGE,
            session=session,
            composition=sl.planning.CompositionOptions(automatic=True, partitions={region: (1, 2)}, pages={region: 1}),
        ),
    )
    assert "first" not in result.bindings
    assert "second" in result.bindings
    assert result.scene.pagers[0].page == 1
    assert session.cursors == {}
    session.apply_updates(result.session_updates)
    assert session.cursor("$/manual").position.page == 1

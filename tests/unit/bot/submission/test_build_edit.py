"""Editing a saved build through the build form's sections, staged until the user applies them."""

from dataclasses import replace
from decimal import Decimal
from inspect import unwrap
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.catalogue.search.contextual import ContextualSearches, search_autocomplete
from squid.bot.submission.cog import BuildCommands
from squid.bot.submission.ui.build_edit import BuildEditScreen
from squid.bot.submission.ui.sections import SectionEditorScreen
from squid.bot.utils.autocomplete import BotInteraction
from squid.builds.application import BuildService
from squid.builds.domain import ShowcaseTag, Status, UnresolvedTaxonomy
from squid.builds.domain.credits import name_credits
from squid.builds.models import Build, DoorBuild, ExtenderBuild, OtherBuild
from squid.core.sentinel import UNSET
from squid.submissions.domain import ChoiceOption, FormManifest
from squid.submissions.domain.forms import VisibilityOperator, VisibilityRule
from squid.suggestions.application import SuggestionRegistry, SuggestionService, SuggestionSource
from squid.suggestions.infrastructure.providers import StaticProvider
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition
from squid_ui.ext.discord.testing import InteractionHarness, assert_within_limits, commit_render, payload_texts, stage
from squid_ui.testing import press_event, texts
from tests.support.build_edit_runner import BuildEditRecorder, build_edit_actor, edit_workspace
from tests.support.discord import make_command_interaction


def editor(build: Build, *, showcase_only: bool = False) -> BuildEditScreen:
    return BuildEditScreen(
        build,
        cast(BuildService, AsyncMock()),
        edit_workspace(build, showcase_only=showcase_only),
        edits=BuildEditRecorder(),
        actor=build_edit_actor,
        authorize=AsyncMock(return_value=True),
        render_build=AsyncMock(return_value=sl.paragraph("Preview")),
        refresh_posts=AsyncMock(),
    )


def rendered(screen: BuildEditScreen) -> str:
    return "\n".join(texts(screen.render()))


def _door(**values: Any) -> DoorBuild:
    if "creators_ign" in values:
        values["credits"] = name_credits(values.pop("creators_ign"), legacy=True)
    return DoorBuild(id=1, submission_status=Status.CONFIRMED, submitter_account_id=1, **values)


@pytest.mark.parametrize("build_type", [DoorBuild, ExtenderBuild, OtherBuild])
async def test_every_category_renders_its_form_sections_within_discord_limits(build_type: Any) -> None:
    screen = editor(build_type(id=1, submission_status=Status.CONFIRMED, submitter_account_id=1))
    await screen.projection.reload()

    assert screen.workspace.sections
    root = sd.MessageRoot(screen, access=sd.Owner(7), timeout=None)
    assert_within_limits(commit_render(root))


async def test_editor_renders_both_histories_when_saved_refresh_fails() -> None:
    screen = editor(_door(version_spec="1.20"))
    cast(AsyncMock, screen.builds.get).side_effect = RuntimeError("reload failed")
    with sl.runtime.transaction() as tx:
        await screen.workspace.save_values(tx, {"version_compatibility": "1.21"})
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("review"))
        await ui.press(ui.key_for("apply"))

        assert screen.controller.refresh_failed
        assert screen.controller.undoable
        assert screen.workspace.history.can_undo
        assert ui.key_for("refresh-saved")


async def test_changes_stay_staged_and_undo_steps_back_through_them() -> None:
    build = _door(version_spec="1.20")
    workspace = editor(build).workspace
    assert workspace.answers["version_compatibility"] == "1.20"

    with sl.runtime.transaction() as tx:
        await workspace.save_values(tx, {"version_compatibility": "1.21"})
    with sl.runtime.transaction() as tx:
        await workspace.save_values(tx, {"credits": [credit.to_json() for credit in name_credits(["Alice", "Bob"])]})

    assert workspace.patch().attributes() == {"version_spec": "1.21", "credits": name_credits(["Alice", "Bob"])}
    assert build.version_spec == "1.20"
    assert build.creators_ign == []
    assert workspace.answers["credits"] == [credit.to_json() for credit in name_credits(["Alice", "Bob"])]
    await workspace.history.undo()
    assert workspace.patch().attributes() == {"version_spec": "1.21"}
    assert workspace.answers["credits"] == []
    assert workspace.answers["version_compatibility"] == "1.21"


@pytest.mark.parametrize("registered", [True, False])
async def test_search_patterns_hands_off_to_the_command_and_stages_an_undoable_result(registered: bool) -> None:
    build = _door(patterns=["Regular"])
    screen = editor(build)
    sources = SuggestionRegistry.of(
        (
            SuggestionSource("approved_patterns", StaticProvider.labelled((("full_lamp", "Full Lamp"),))),
            SuggestionSource("search_query", StaticProvider.of(("width:",))),
        )
    )
    async with stage(screen, owner=object()) as ui:
        client = ui.interaction.client
        client.contextual_searches = ContextualSearches()
        client.services = SimpleNamespace(suggestions=SuggestionService(sources))
        registration = SimpleNamespace(name="search", type=discord.AppCommandType.chat_input, mention="</search:2>")
        commands = [registration] if registered else []
        command = "</search:2>" if registered else "`/search`"
        with patch.object(client.tree, "fetch_commands", new=AsyncMock(return_value=commands)):
            launch = await ui.press(ui.key_for("search-patterns"))
        assert launch.deferrals[0].kwargs["ephemeral"] is True
        prompt = launch.edits[0].kwargs["content"]
        assert prompt.endswith(f"Use {command} here within two minutes. Leave `scope` empty.")
        assert prompt.count("/search") == 1

        autocomplete = make_command_interaction(client, name="search", user_id=1, channel_id=5, autocomplete=True)
        (pattern,) = await search_autocomplete(cast(BotInteraction, autocomplete), "")
        assert pattern.name == "Full Lamp"
        assert isinstance(pattern.value, str)
        for user_id, channel_id in ((2, 5), (1, 6)):
            autocomplete = make_command_interaction(
                client, name="search", user_id=user_id, channel_id=channel_id, autocomplete=True
            )
            assert [choice.value for choice in await search_autocomplete(cast(BotInteraction, autocomplete), "")] == [
                "width:"
            ]

        # A slash command is owned by its cog, which can differ from the editor's owner.
        scope = client.ui.scope(object())
        cog = BuildCommands.__new__(BuildCommands)
        invoke = unwrap(cog.search_records.callback)
        no_matches = InteractionHarness(client=client, message_id=100)
        await invoke(cog, await scope.request(no_matches.source), query="zzzzz")
        assert payload_texts(no_matches.edits[0].kwargs["view"]) == [f"No matches. Try another query with {command}."]

        invocation = InteractionHarness(client=client, message_id=101)
        await invoke(cog, await scope.request(invocation.source), query=pattern.value)
        assert invocation.deferrals[0].kwargs["ephemeral"] is True
        key = sd.SessionKey.custom("contextual-search", (1, 5))
        (session,) = client.ui.sessions.get(key)
        root = session.root
        (match,) = (key for key in root.snapshot().handler_keys if key == "match" or key.endswith(".match"))

        selection = InteractionHarness(client=client, message_id=101)
        await root.dispatch(match, selection.source, ["0"], generation=root.generation)
        assert root.finished
        assert not ui.root.finished
        assert screen.workspace.answers["patterns"] == ["regular", "full_lamp"]
        assert build.patterns == ["Regular"]
        assert client.contextual_searches.active(invocation.source) is None
        await screen.workspace.history.undo()
        assert screen.workspace.answers["patterns"] == ["regular"]

        stale = InteractionHarness(client=client, message_id=101)
        request = await client.ui.scope(object()).request(stale.source)
        assert await unwrap(cog.search_records.callback)(cog, request, query=pattern.value) is None
        assert stale.sends[0].kwargs["ephemeral"] is True
        assert "This search ended" in str(stale.sends[0].kwargs["view"].to_components())


def test_editor_derivations_share_normalization_without_sharing_answer_dicts() -> None:
    workspace = edit_workspace(_door(version_spec="1.20"))
    with patch.object(
        FormManifest, "apply_defaults", autospec=True, side_effect=FormManifest.apply_defaults
    ) as normalize:
        answers = workspace.answers
        fields = workspace.fields
        sections = workspace.sections
        answers["version_compatibility"] = "changed by caller"

        assert workspace.answers["version_compatibility"] == "1.20"
        assert workspace.fields is fields
        assert workspace.sections is sections
        assert normalize.call_count == 1

        assert workspace.prefill({"version_compatibility": "1.21"}) is None
        assert workspace.answers["version_compatibility"] == "1.21"
        assert workspace.fields == fields
        assert workspace.sections == sections

        workspace.reset(_door(version_spec="1.22"))
        assert workspace.answers["version_compatibility"] == "1.22"


def test_editor_visibility_follows_staged_answers_and_rollback() -> None:
    workspace = edit_workspace(_door(version_spec="1.20"))
    workspace.manifest = replace(
        workspace.manifest,
        common_sections=tuple(
            replace(
                section,
                fields=tuple(
                    replace(
                        field, visible_when=VisibilityRule("version_compatibility", VisibilityOperator.EQUALS, "1.21")
                    )
                    if field.id == "display_name"
                    else field
                    for field in section.fields
                ),
            )
            for section in workspace.manifest.common_sections
        ),
    )
    assert "display_name" not in {field.id for field in workspace.fields}
    before = workspace.sections

    def stage_and_abort() -> None:
        with sl.runtime.transaction():
            assert workspace.prefill({"version_compatibility": "1.21"}) is None
            assert "display_name" in {field.id for field in workspace.fields}
            assert "display_name" in {field.id for section in workspace.sections for field in section.fields}
            raise ValueError("abort")

    with pytest.raises(ValueError, match="abort"):
        stage_and_abort()
    assert workspace.answers["version_compatibility"] == "1.20"
    assert workspace.sections is before
    assert "display_name" not in {field.id for field in workspace.fields}

    with sl.runtime.transaction():
        assert workspace.prefill({"version_compatibility": "1.21"}) is None
    assert "display_name" in {field.id for field in workspace.fields}
    assert "display_name" in {field.id for section in workspace.sections for field in section.fields}


async def test_an_invalid_value_stages_nothing_and_says_why() -> None:
    workspace = editor(_door()).workspace

    with sl.runtime.transaction() as tx:
        await workspace.save_values(tx, {"display_name": "x" * 500})

    assert workspace.patch().attributes() == {}
    assert "Too long" in sl.text.source_text(workspace.notice)


async def test_preview_includes_staged_values_without_changing_the_stored_build() -> None:
    build = _door(version_spec="1.20")
    screen = editor(build)
    assert screen.workspace.prefill({"version_compatibility": "1.21"}) is None
    screen.open_child = AsyncMock()  # type: ignore[method-assign]

    await screen._preview(cast(sd.PressEvent, press_event()))

    preview = cast(AsyncMock, screen._render_build).call_args.args[0]
    assert preview.version_spec == "1.21"
    assert preview is not build
    assert build.version_spec == "1.20"


async def test_review_lists_only_changed_fields_including_cleared_ones() -> None:
    screen = editor(_door(version_spec="1.20", creators_ign=["Alice"], completion_time="2024"))
    await screen.projection.reload()
    assert screen.workspace.prefill({"version_compatibility": None}) is None
    screen.confirming = True

    review = rendered(screen)

    assert "Works in versions: *Not set*" in review
    assert "Alice" not in review
    assert "2024" not in review


async def test_names_outside_the_catalogue_are_listed_and_can_only_be_removed() -> None:
    build = _door(component_restrictions=["Observerless"], miscellaneous_restrictions=["Hand-wired"])
    screen = editor(build)
    await screen.projection.reload()
    workspace = screen.workspace

    assert "Not in the catalogue: Hand-wired" in rendered(screen)
    # Kept names are written back, so an unrelated save never drops them.
    with sl.runtime.transaction() as tx:
        await workspace.save_values(tx, {"display_name": "Door"})
    assert workspace.patch().miscellaneous_restrictions is UNSET

    with sl.runtime.transaction() as tx:
        workspace.remove_unresolved(tx, {"Hand-wired"})

    assert workspace.unresolved == UnresolvedTaxonomy()
    assert workspace.patch().miscellaneous_restrictions == ["Observerless"]
    screen.confirming = True
    assert "Removed: Hand-wired" in rendered(screen)


async def test_a_showcase_only_editor_offers_just_the_tags() -> None:
    build = _door(version_spec="1.20", miscellaneous_restrictions=["Hand-wired"])
    screen = editor(build, showcase_only=True)
    await screen.projection.reload()
    workspace = screen.workspace

    assert [field.id for section in workspace.sections for field in section.fields] == ["showcase_tags"]
    text = rendered(screen)
    assert "Edit tags" in text
    assert "Hand-wired" not in text
    with sl.runtime.transaction() as tx:
        await workspace.save_values(tx, {"showcase_tags": [{"key": "compact"}]})
    assert workspace.patch().attributes() == {"showcase_tags": [ShowcaseTag("compact")]}


async def test_picking_showcase_tags_keeps_the_values_of_tags_that_stay() -> None:
    speed = TagDefinition(
        stable_key="speed",
        display_name="Speed",
        authority=TagAuthority.USER,
        semantic_kind=TagSemanticKind.SHOWCASE,
        value_type=TagValueType.NUMERIC,
        moderation_status=TagModerationStatus.APPROVED,
    )
    screen = editor(_door(tags=[TagAssignment(speed, Decimal("1.5"))]))
    await screen.projection.reload()
    section = next(
        section for section in screen.workspace.sections if "showcase_tags" in {f.id for f in section.fields}
    )
    child = SectionEditorScreen(screen.workspace, section.id)
    assert "speed: 1.5" in _payload(child)

    with sl.runtime.transaction() as tx:
        event = Mock(spec=sd.SelectionEvent, transaction=tx, values=("compact",))
        await child._select_value("showcase_tags", event, visible=("compact",))

    assert screen.workspace.answers["showcase_tags"] == [{"key": "speed", "value": "1.5"}, {"key": "compact"}]
    assert screen.workspace.patch().attributes() == {
        "showcase_tags": [ShowcaseTag("speed", "1.5"), ShowcaseTag("compact")]
    }


_VALUED_TAGS = (
    ChoiceOption("compact", "Compact"),
    ChoiceOption("speed", "Speed", "numeric"),
    ChoiceOption("lit", "Lit", "boolean"),
)


def _payload(screen: sd.Screen[None]) -> str:
    """The display text of a screen's committed render, which must fit Discord's limits."""
    built = commit_render(sd.MessageRoot(screen, access=sd.Owner(7), timeout=None))
    assert_within_limits(built)
    return "\n".join(payload_texts(built))


def _showcase_editor(screen: BuildEditScreen, options: tuple[ChoiceOption, ...]) -> SectionEditorScreen:
    screen.workspace.option_sets = {**screen.workspace.option_sets, "approved_showcase_tags": options}
    (section,) = screen.workspace.sections
    return SectionEditorScreen(screen.workspace, section.id)


async def test_a_showcase_only_editor_sets_tag_values_checks_them_and_undoes_them() -> None:
    screen = editor(_door(), showcase_only=True)
    await screen.projection.reload()
    workspace = screen.workspace
    child = _showcase_editor(screen, _VALUED_TAGS)
    with sl.runtime.transaction() as tx:
        event = Mock(spec=sd.SelectionEvent, transaction=tx, values=("compact", "speed"))
        await child._select_value("showcase_tags", event, visible=("compact", "speed", "lit"))
    assert "Speed: ⚠️ *Needs a value*" in _payload(child)
    assert "Speed: ⚠️ *Needs a value*" in rendered(screen)

    press = Mock(spec=sd.SelectionEvent, values=("speed",), present_form=AsyncMock())
    await child.showcase_tags["showcase_tags"]._open_value_form(press)
    form = press.present_form.call_args
    spec: sl.forms.FormSpec[Any] = form.args[0]

    rejected = await spec.evaluate({"value": "fast"})
    assert "expects a number" in sl.text.source_text(rejected.errors[0].message)
    accepted = await spec.evaluate({"value": " 1.50 "})
    with sl.runtime.transaction() as tx:
        await form.kwargs["on_submit"](Mock(spec=sl.SubmitEvent, transaction=tx, values=accepted.values))

    assert workspace.patch().attributes() == {"showcase_tags": [ShowcaseTag("compact"), ShowcaseTag("speed", "1.50")]}
    assert "Speed: 1.50" in _payload(SectionEditorScreen(workspace, child.section_id))
    await workspace.history.undo()
    assert workspace.answers["showcase_tags"] == [{"key": "compact"}, {"key": "speed"}]


async def test_a_boolean_tag_value_is_chosen_as_yes_or_no() -> None:
    screen = editor(_door(), showcase_only=True)
    child = _showcase_editor(screen, _VALUED_TAGS)
    assert screen.workspace.prefill({"showcase_tags": [{"key": "lit"}]}) is None
    press = Mock(spec=sd.SelectionEvent, values=("lit",), present_form=AsyncMock())

    await child.showcase_tags["showcase_tags"]._open_value_form(press)

    spec: sl.forms.FormSpec[Any] = press.present_form.call_args.args[0]
    assert (await spec.evaluate({"value": "false"})).values == {"value": "false"}


async def test_many_valued_showcase_tags_render_within_discord_limits() -> None:
    options = tuple(ChoiceOption(f"tag_{index}", f"Tag {index}", "text") for index in range(40))
    screen = editor(_door(), showcase_only=True)
    child = _showcase_editor(screen, options)
    tags = [{"key": option.value, "value": "x" * 300} for option in options]
    assert screen.workspace.prefill({"showcase_tags": tags}) is None

    assert "Tag 0: " in _payload(child)

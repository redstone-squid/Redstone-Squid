"""The factory layer: what it normalizes, and what it refuses to guess."""

from datetime import UTC, datetime
from typing import cast

import pytest

import squid_ui as sl
from squid_ui.semantic import SemanticNode
from squid_ui_discord import DISCORD_V2_DPY27


async def _noop(_event) -> None: ...


def _responder() -> sl.interactions.ActionResponder:
    """A responder no factory test reaches; rating only reads the event's fields."""
    return cast(sl.interactions.ActionResponder, object())


class TestNormalization:
    def test_omitted_children_are_skipped(self) -> None:
        node = sl.section(sl.heading("H"), sl.paragraph("kept"), None, False, sl.paragraph("also kept"))  # noqa: FBT003

        assert node.children == (sl.semantic.Paragraph("kept"), sl.semantic.Paragraph("also kept"))

    def test_conditional_children_compose_with_and(self) -> None:
        shows_extra = False

        node = sl.section(sl.heading("H"), sl.paragraph("always"), shows_extra and sl.paragraph("sometimes"))

        assert node.children == (sl.semantic.Paragraph("always"),)

    def test_text_is_promoted_to_a_paragraph(self) -> None:
        node = sl.section(sl.heading("H"), "bare markdown", sl.plain("literal"))

        assert node.children == (sl.semantic.Paragraph("bare markdown"), sl.semantic.Paragraph(sl.plain("literal")))

    def test_t_strings_are_resolved_with_escaped_interpolations(self) -> None:
        hostile = "*not bold*"

        node = sl.section(sl.heading("H"), t"value: {hostile}")

        assert node.children == (sl.semantic.Paragraph(sl.md(t"value: {hostile}")),)
        promoted = node.children[0]
        assert isinstance(promoted, sl.semantic.Paragraph)
        assert isinstance(promoted.content, sl.text.ResolvedText)
        assert "\\*not bold\\*" in promoted.content.content

    def test_t_strings_are_resolved_in_configuration_text_too(self) -> None:
        name = "a_b"

        assert sl.section(sl.heading(t"{name}")).heading == sl.semantic.Heading(sl.md(t"{name}"))

    def test_empty_containers_are_pruned_by_the_planner(self) -> None:
        shown = False

        result = sl.planning.plan(sl.stack(None, shown and sl.paragraph("x")), target=DISCORD_V2_DPY27)

        assert result.scene.components_v2.children == ()


class TestRefusals:
    def test_true_is_rejected_rather_than_skipped(self) -> None:
        with pytest.raises(TypeError, match="True is not content"):
            sl.section(sl.heading("H"), sl.paragraph("a"), True)  # type: ignore[bad-argument-type]  # noqa: FBT003

    def test_a_sequence_asks_to_be_unpacked(self) -> None:
        rows = [sl.paragraph("a"), sl.paragraph("b")]

        with pytest.raises(TypeError, match=r"unpack it, e\.g\. sl\.section\(\*entries\)"):
            sl.section(sl.heading("H"), rows)  # type: ignore[arg-type]

    def test_a_generator_asks_to_be_unpacked(self) -> None:
        with pytest.raises(TypeError, match="unpack it"):
            sl.section(sl.heading("H"), (sl.paragraph(str(index)) for index in range(2)))  # type: ignore[arg-type]

    def test_a_mapping_names_its_own_unpacking(self) -> None:
        with pytest.raises(TypeError, match=r"unpack what you meant.*mapping\.values\(\)"):
            sl.section(sl.heading("H"), {"a": sl.paragraph("a")})  # type: ignore[arg-type]

    def test_a_component_is_pointed_at_a_boundary(self) -> None:
        class Child(sl.Component):
            def render(self):
                return sl.paragraph("child")

        with pytest.raises(TypeError, match=r"self\.boundary\(child, key=\.\.\.\)"):
            sl.section(sl.heading("H"), Child())  # type: ignore[arg-type]

    def test_a_foreign_value_names_its_position(self) -> None:
        with pytest.raises(TypeError, match=r"sl\.section\(\) argument 1: int is not content"):
            sl.section(sl.heading("H"), sl.paragraph("a"), 3)  # type: ignore[arg-type]

    def test_collections_refuse_foreign_elements(self) -> None:
        with pytest.raises(TypeError, match=r"sl\.action_controls\(\) argument 0: text is not an entry here"):
            sl.action_controls("Vote", key="votes")  # type: ignore[arg-type]

    def test_collections_still_ask_sequences_to_unpack(self) -> None:
        with pytest.raises(TypeError, match=r"unpack it, e\.g\. sl\.action_controls\(\*entries\)"):
            sl.action_controls([sl.action_control("Vote", _noop, key="vote")], key="votes")  # type: ignore[arg-type]


class TestParity:
    """Every factory is sugar: its output is the dataclass an author would have written."""

    def test_containers(self) -> None:
        assert sl.group("a") == sl.semantic.Group((sl.semantic.Paragraph("a"),))
        assert sl.stack("a") == sl.semantic.Stack((sl.semantic.Paragraph("a"),))
        assert sl.cluster("a") == sl.semantic.Cluster((sl.semantic.Paragraph("a"),))
        assert sl.block("a") == sl.semantic.Block((sl.semantic.Paragraph("a"),))
        assert sl.section(sl.heading("H"), "a") == sl.semantic.Section(
            sl.semantic.Heading("H"), (sl.semantic.Paragraph("a"),)
        )
        assert sl.article(sl.heading("H"), "a") == sl.semantic.Article(
            sl.semantic.Heading("H"), (sl.semantic.Paragraph("a"),)
        )
        assert sl.aside("a", tone=sl.Tone.WARNING) == sl.semantic.Aside((sl.semantic.Paragraph("a"),), sl.Tone.WARNING)
        assert sl.details(sl.summary("S"), "a", key="k", open=sl.uncontrolled(initial=True)) == sl.semantic.Details(
            "k", sl.semantic.Summary("S"), (sl.semantic.Paragraph("a"),), sl.semantic.Uncontrolled(initial=True)
        )
        assert sl.item(sl.item_label("L"), "a", key="k") == sl.semantic.Item(
            "k", sl.semantic.ItemLabel("L"), (sl.semantic.Paragraph("a"),)
        )

    def test_leaves(self) -> None:
        assert sl.heading("H", level=3) == sl.semantic.Heading("H", 3)
        assert sl.paragraph("p") == sl.semantic.Paragraph("p")
        assert sl.status("s", tone=sl.Tone.DANGER) == sl.semantic.Status("s", sl.Tone.DANGER)
        assert sl.code("x = 1", language="python") == sl.semantic.Code("x = 1", "python")
        assert sl.quote("q", attribution="me") == sl.semantic.Quote("q", "me")
        assert sl.progress(0.5, label="L") == sl.semantic.ProgressBar(0.5, "L")
        assert sl.metric(3, "Blocks", unit="s") == sl.semantic.Metric(3, "Blocks", "s")
        instant = datetime(2026, 8, 22, tzinfo=UTC)
        assert sl.timestamp(instant, style=sl.semantic.TimeStyle.RELATIVE, label="Updated") == sl.semantic.Timestamp(
            instant, sl.semantic.TimeStyle.RELATIVE, "Updated"
        )
        zoned = sl.temporal.ZonedDateTime(instant, "America/New_York")
        assert sl.zoned_timestamp(zoned, label="Starts") == sl.semantic.ZonedTimestamp(zoned, "Starts")
        assert sl.figure("https://example.invalid/a.png") == sl.semantic.Figure(
            sl.semantic.MediaItem("", "https://example.invalid/a.png")
        )

    def test_collections(self) -> None:
        assert sl.fields(sl.field("L", "V")) == sl.semantic.Fields((sl.semantic.Field("", "L", "V"),))
        assert sl.bullets("a", key="k") == sl.semantic.List((sl.semantic.ListItem("", "a"),), "k")
        assert sl.media("https://example.invalid/a.png", key="k") == sl.semantic.Media(
            (sl.semantic.MediaItem("", "https://example.invalid/a.png"),), "k"
        )
        assert sl.table(
            sl.columns(sl.column("A"), sl.column("B")), sl.table_row("1", "2"), key="k"
        ) == sl.semantic.Table(
            sl.semantic.Columns((sl.semantic.Column("", "A"), sl.semantic.Column("", "B"))),
            (sl.semantic.TableRow("", ("1", "2")),),
            "k",
        )
        assert sl.items(sl.item(sl.item_label("L"), key="i"), key="k") == sl.semantic.Items(
            "k", (sl.semantic.Item("i", sl.semantic.ItemLabel("L"), ()),)
        )

    def test_table_requires_columns(self) -> None:
        with pytest.raises(ValueError, match="at least one column"):
            sl.columns()

    def test_table_rows_match_the_column_count(self) -> None:
        with pytest.raises(ValueError, match="2 cells for 1 columns"):
            sl.table(sl.columns(sl.column("A")), sl.table_row("1", "2"), key="k")

    def test_controls(self) -> None:
        assert sl.action_control("Vote", _noop, key="vote") == sl.semantic.ActionControl("vote", "Vote", _noop)
        assert sl.link("Docs", "https://example.invalid", key="docs") == sl.semantic.Link(
            "docs", "Docs", "https://example.invalid"
        )
        assert sl.control_group(sl.action_control("Vote", _noop, key="vote"), key="g") == sl.semantic.ControlGroup(
            "g", (sl.semantic.ActionControl("vote", "Vote", _noop),)
        )
        assert sl.action_controls(sl.action_control("Vote", _noop, key="vote"), key="a") == sl.semantic.ActionControls(
            (sl.semantic.ActionControl("vote", "Vote", _noop),), "a"
        )
        assert sl.choice("Yes", key="y", description="d") == sl.semantic.Choice("y", "Yes", "d")
        assert sl.choices(
            sl.choice("Yes", key="y"), key="c", selection=sl.controlled(("y",), _noop)
        ) == sl.semantic.Choices("c", (sl.semantic.Choice("y", "Yes"),), sl.semantic.Controlled(("y",), _noop))
        assert sl.routed_choices(sl.choice("Yes", key="y"), key="c", route_id="r:choices") == sl.semantic.RoutedChoices(
            "c", (sl.semantic.Choice("y", "Yes"),), "r:choices"
        )
        assert sl.nav_option("Home", key="home") == sl.semantic.NavOption("home", "Home")
        assert sl.navigation(
            sl.nav_option("Home", key="home"), key="n", current=sl.controlled("home", _noop)
        ) == sl.semantic.Navigation(
            "n", (sl.semantic.NavOption("home", "Home"),), sl.semantic.Controlled("home", _noop)
        )


class TestDrift:
    _ALIASES = {
        "ActionControl": "action_control",
        "ActionControls": "action_controls",
        "ControlGroup": "control_group",
        "FormTrigger": "form",
        "Metric": "metric",
        "List": "bullets",
        "ProgressBar": "progress",
        "RoutedChoices": "routed_choices",
        "ZonedTimestamp": "zoned_timestamp",
    }

    def test_every_semantic_node_has_a_root_level_factory(self) -> None:
        for member in SemanticNode.__value__.__args__:
            name = self._ALIASES.get(member.__name__, member.__name__.lower())
            assert name in sl.__all__, f"{member.__name__} has no exported factory"
            assert callable(getattr(sl, name))


def test_timestamp_factory_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="aware"):
        sl.timestamp(datetime(2026, 8, 22))  # noqa: DTZ001 - deliberately exercises naive rejection


class TestParityWithCards:
    def test_a_section_takes_house_colour_and_a_lead_image(self) -> None:
        node = sl.section(sl.heading("Title"), "body", accent=0x43B581, thumbnail="https://example.invalid/a.png")

        assert node.accent == 0x43B581
        assert node.thumbnail == "https://example.invalid/a.png"

    def test_field_fallbacks_are_resolved_like_any_other_text(self) -> None:
        node = sl.field("Videos", "a, b, c", fallbacks=(t"{3} videos", "3"))

        assert node.fallbacks == (sl.md(t"{3} videos"), "3")

    def test_note_is_small_print(self) -> None:
        assert sl.note("Submission ID: 5") == sl.semantic.Note("Submission ID: 5", sl.semantic.Importance.LOW)


class TestRating:
    def test_points_are_stars_while_the_control_is_a_button_row(self) -> None:
        node = sl.rating(key="stars")

        assert [choice.key for choice in node.choices] == ["1", "2", "3", "4", "5"]
        assert [choice.label for choice in node.choices] == ["★", "★★", "★★★", "★★★★", "★★★★★"]
        assert (node.minimum, node.maximum) == (1, 1)

    def test_a_wider_scale_numbers_its_points(self) -> None:
        node = sl.rating(key="score", maximum=10)

        assert [choice.label for choice in node.choices] == [str(point) for point in range(1, 11)]

    def test_named_points_replace_their_stars(self) -> None:
        node = sl.rating(key="stars", labels={1: "Poor", 5: t"Excellent"})

        assert node.choices[0].label == "Poor"
        assert node.choices[1].label == "★★"
        assert node.choices[4].label == sl.md(t"Excellent")

    def test_a_managed_value_seeds_the_selection_as_its_option_key(self) -> None:
        assert sl.rating(key="stars").selection == sl.semantic.Uncontrolled(())
        assert sl.rating(key="stars", value=sl.uncontrolled(3)).selection == sl.semantic.Uncontrolled(("3",))

    async def test_a_controlled_value_round_trips_through_a_typed_event(self) -> None:
        seen: list[int] = []

        async def rate(event: sl.ScaleEvent) -> None:
            seen.append(event.value)

        node = sl.rating(key="stars", value=sl.controlled(2, rate))
        assert isinstance(node.selection, sl.semantic.Controlled)
        assert node.selection.value == ("2",)

        actor = sl.interactions.Actor("7")
        await node.selection.on_change(sl.ChoiceEvent(actor, _responder(), None, {}, ("4",)))
        assert seen == [4]

    async def test_a_cleared_selection_reaches_no_handler(self) -> None:
        rated = False

        async def rate(event: sl.ScaleEvent) -> None:
            nonlocal rated
            rated = True

        node = sl.rating(key="stars", value=sl.controlled(None, rate))
        assert isinstance(node.selection, sl.semantic.Controlled)
        assert node.selection.value == ()

        await node.selection.on_change(sl.ChoiceEvent(sl.interactions.Actor("7"), _responder(), None, {}, ()))
        assert not rated

    def test_a_scale_needs_at_least_two_points(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            sl.rating(key="stars", maximum=1)

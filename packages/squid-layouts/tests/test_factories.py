"""The factory layer: what it normalizes, and what it refuses to guess."""

import pytest

import squid_layouts as sl
from squid_layouts.discord import DEFAULT_TARGET
from squid_layouts.semantic import SemanticNode


async def _noop(_event) -> None: ...


class TestNormalization:
    def test_omitted_children_are_skipped(self) -> None:
        node = sl.section(sl.paragraph("kept"), None, False, sl.paragraph("also kept"))  # noqa: FBT003

        assert node.children == (sl.Paragraph("kept"), sl.Paragraph("also kept"))

    def test_conditional_children_compose_with_and(self) -> None:
        shows_extra = False

        node = sl.section(sl.paragraph("always"), shows_extra and sl.paragraph("sometimes"))

        assert node.children == (sl.Paragraph("always"),)

    def test_text_is_promoted_to_a_paragraph(self) -> None:
        node = sl.section("bare markdown", sl.plain("literal"))

        assert node.children == (sl.Paragraph("bare markdown"), sl.Paragraph(sl.plain("literal")))

    def test_t_strings_are_resolved_with_escaped_interpolations(self) -> None:
        hostile = "*not bold*"

        node = sl.section(t"value: {hostile}")

        assert node.children == (sl.Paragraph(sl.md(t"value: {hostile}")),)
        promoted = node.children[0]
        assert isinstance(promoted, sl.Paragraph)
        assert isinstance(promoted.content, sl.ResolvedText)
        assert "\\*not bold\\*" in promoted.content.content

    def test_t_strings_are_resolved_in_configuration_text_too(self) -> None:
        name = "a_b"

        assert sl.section(heading=t"{name}").heading == sl.md(t"{name}")

    def test_empty_containers_are_pruned_by_the_planner(self) -> None:
        shown = False

        result = sl.plan(sl.section(None, shown and sl.paragraph("x")), target=DEFAULT_TARGET)

        assert result.scene.children == ()


class TestRefusals:
    def test_true_is_rejected_rather_than_skipped(self) -> None:
        with pytest.raises(TypeError, match="True is not content"):
            sl.section(sl.paragraph("a"), True)  # type: ignore[bad-argument-type]  # noqa: FBT003

    def test_a_sequence_asks_to_be_unpacked(self) -> None:
        rows = [sl.paragraph("a"), sl.paragraph("b")]

        with pytest.raises(TypeError, match=r"unpack it, e\.g\. sl\.section\(\*entries\)"):
            sl.section(rows)  # type: ignore[arg-type]

    def test_a_generator_asks_to_be_unpacked(self) -> None:
        with pytest.raises(TypeError, match="unpack it"):
            sl.section(sl.paragraph(str(index)) for index in range(2))  # type: ignore[arg-type]

    def test_a_mapping_names_its_own_unpacking(self) -> None:
        with pytest.raises(TypeError, match=r"unpack what you meant.*mapping\.values\(\)"):
            sl.section({"a": sl.paragraph("a")})  # type: ignore[arg-type]

    def test_a_component_is_pointed_at_embed(self) -> None:
        class Child(sl.Component):
            def render(self):
                return sl.paragraph("child")

        with pytest.raises(TypeError, match=r"self\.embed\(child, key=\.\.\.\)"):
            sl.section(Child())  # type: ignore[arg-type]

    def test_a_foreign_value_names_its_position(self) -> None:
        with pytest.raises(TypeError, match=r"sl\.section\(\) argument 1: int is not content"):
            sl.section(sl.paragraph("a"), 3)  # type: ignore[arg-type]

    def test_collections_refuse_foreign_elements(self) -> None:
        with pytest.raises(TypeError, match=r"sl\.actions\(\) argument 0: text is not an entry here"):
            sl.actions("Vote", key="votes")  # type: ignore[arg-type]

    def test_collections_still_ask_sequences_to_unpack(self) -> None:
        with pytest.raises(TypeError, match=r"unpack it, e\.g\. sl\.actions\(\*entries\)"):
            sl.actions([sl.action("Vote", _noop, key="vote")], key="votes")  # type: ignore[arg-type]


class TestParity:
    """Every factory is sugar: its output is the dataclass an author would have written."""

    def test_containers(self) -> None:
        assert sl.group("a") == sl.Group((sl.Paragraph("a"),))
        assert sl.stack("a") == sl.Stack((sl.Paragraph("a"),))
        assert sl.cluster("a") == sl.Cluster((sl.Paragraph("a"),))
        assert sl.section("a", heading="H") == sl.Section((sl.Paragraph("a"),), "H")
        assert sl.article("a", heading="H") == sl.Article((sl.Paragraph("a"),), "H")
        assert sl.aside("a", tone=sl.Tone.WARNING) == sl.Aside((sl.Paragraph("a"),), sl.Tone.WARNING)
        assert sl.details("a", key="k", summary="S", open=True) == sl.Details("k", "S", (sl.Paragraph("a"),), True)  # noqa: FBT003
        assert sl.item("a", key="k", label="L") == sl.Item("k", "L", (sl.Paragraph("a"),))

    def test_leaves(self) -> None:
        assert sl.heading("H", level=3) == sl.Heading("H", 3)
        assert sl.paragraph("p") == sl.Paragraph("p")
        assert sl.status("s", tone=sl.Tone.DANGER) == sl.Status("s", sl.Tone.DANGER)
        assert sl.code("x = 1", language="python") == sl.Code("x = 1", "python")
        assert sl.quote("q", attribution="me") == sl.Quote("q", "me")
        assert sl.progress(0.5, label="L") == sl.Progress(0.5, "L")
        assert sl.measure(3, "Blocks", unit="s") == sl.Measure(3, "Blocks", "s")
        assert sl.figure("https://example.invalid/a.png") == sl.Figure(
            sl.MediaItem("", "https://example.invalid/a.png")
        )

    def test_collections(self) -> None:
        assert sl.fields(sl.field("L", "V")) == sl.Fields((sl.Field("", "L", "V"),))
        assert sl.bullets("a", key="k") == sl.List((sl.ListItem("", "a"),), "k")
        assert sl.media("https://example.invalid/a.png", key="k") == sl.Media(
            (sl.MediaItem("", "https://example.invalid/a.png"),), "k"
        )
        assert sl.table(sl.table_row("1", "2"), columns=(sl.column("A"), sl.column("B")), key="k") == sl.Table(
            (sl.Column("", "A"), sl.Column("", "B")), (sl.TableRow("", ("1", "2")),), "k"
        )
        assert sl.items(sl.item(key="i", label="L"), key="k") == sl.Items("k", (sl.Item("i", "L", ()),))

    def test_controls(self) -> None:
        assert sl.action("Vote", _noop, key="vote") == sl.Action("vote", "Vote", _noop)
        assert sl.link("Docs", "https://example.invalid", key="docs") == sl.Link(
            "docs", "Docs", "https://example.invalid"
        )
        assert sl.action_group(sl.action("Vote", _noop, key="vote"), key="g") == sl.ActionGroup(
            "g", (sl.Action("vote", "Vote", _noop),)
        )
        assert sl.actions(sl.action("Vote", _noop, key="vote"), key="a") == sl.Actions(
            (sl.Action("vote", "Vote", _noop),), "a"
        )
        assert sl.choice("Yes", key="y", description="d") == sl.Choice("y", "Yes", "d")
        assert sl.choices(sl.choice("Yes", key="y"), key="c", selected=["y"], on_change=_noop) == sl.Choices(
            "c", (sl.Choice("y", "Yes"),), ("y",), _noop
        )
        assert sl.destination("Home", key="home") == sl.Destination("home", "Home")
        assert sl.navigation(
            sl.destination("Home", key="home"), key="n", current="home", on_navigate=_noop
        ) == sl.Navigation("n", (sl.Destination("home", "Home"),), "home", _noop)


class TestDrift:
    _ALIASES = {"List": "bullets"}

    def test_every_semantic_node_has_a_root_level_factory(self) -> None:
        for member in SemanticNode.__value__.__args__:
            name = self._ALIASES.get(member.__name__, member.__name__.lower())
            assert name in sl.__all__, f"{member.__name__} has no exported factory"
            assert callable(getattr(sl, name))


class TestParityWithCards:
    def test_a_section_takes_house_colour_and_a_lead_image(self) -> None:
        node = sl.section("body", heading="Title", accent=0x43B581, thumbnail="https://example.invalid/a.png")

        assert node.accent == 0x43B581
        assert node.thumbnail == "https://example.invalid/a.png"

    def test_field_fallbacks_are_resolved_like_any_other_text(self) -> None:
        node = sl.field("Videos", "a, b, c", fallbacks=(t"{3} videos", "3"))

        assert node.fallbacks == (sl.md(t"{3} videos"), "3")

    def test_note_is_small_print(self) -> None:
        assert sl.note("Submission ID: 5") == sl.Note("Submission ID: 5", sl.Importance.LOW)

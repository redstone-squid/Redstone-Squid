"""The classic Discord target: its limits, its capabilities, and its exact primitives."""

import discord
import pytest

import squid_layouts as sl
from squid_discord import CLASSIC_TARGET, V2_LIMITS, V2_TARGET, Target
from squid_layouts.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.planning import plan
from squid_layouts.planning.limits import (
    ATTACHMENTS,
    CLASSIC_LIMITS,
    COMPONENTS,
    CONTENT_TEXT,
    CONTROLS,
    DISPLAY_TEXT,
    EMBED_TEXT,
    EMBEDS,
    ROWS,
)
from squid_layouts.primitives import (
    Card,
    CardAuthor,
    CardField,
    CardFooter,
    CardMedia,
    Content,
    Gallery,
    LinkButton,
    Never,
    Panel,
    Row,
    Section,
    Text,
    Thumbnail,
    Truncate,
)
from squid_layouts.scene.model import SceneClassicMessage, SceneEmbedField


def body(document, target=CLASSIC_TARGET) -> SceneClassicMessage:
    resolved = plan(document, target=target).scene.body
    assert isinstance(resolved, SceneClassicMessage)
    return resolved


class TestTargets:
    def test_the_two_targets_are_separate_identities(self) -> None:
        assert V2_TARGET.id == "discord.components-v2"
        assert CLASSIC_TARGET.id == "discord.components-v1"

    def test_a_target_cannot_be_built_without_saying_which_mode_it_is(self) -> None:
        """The mode decides the dialect, the renderer, and the view type. It is not optional."""
        with pytest.raises(TypeError):
            Target()  # type: ignore[call-arg]

    def test_custom_limits_arrive_through_a_custom_target(self) -> None:
        target = Target.classic(limits=CLASSIC_LIMITS.__class__(embeds=2))

        assert target.capacity(EMBEDS) == 2
        assert target.capacity(EMBED_TEXT) == CLASSIC_LIMITS.embed_text


class TestCapabilities:
    def test_classic_declares_content_and_embeds_and_v2_does_not(self) -> None:
        assert {"message.content", "layout.embed", "layout.embed_fields"} <= CLASSIC_TARGET.capabilities
        assert not {"message.content", "layout.embed"} & V2_TARGET.capabilities

    def test_classic_declares_no_v2_container_structures(self) -> None:
        assert not {"layout.container", "layout.section", "layout.gallery"} & CLASSIC_TARGET.capabilities

    def test_both_targets_share_the_control_and_form_capabilities(self) -> None:
        shared = {"actions.buttons", "actions.select", "forms.modal"}

        assert shared <= CLASSIC_TARGET.capabilities
        assert shared <= V2_TARGET.capabilities


class TestBudgets:
    def test_classic_budgets_two_independent_text_pools(self) -> None:
        assert CLASSIC_LIMITS.text_axes == {CONTENT_TEXT: 2000, EMBED_TEXT: 6000}

    def test_v2_budgets_exactly_one(self) -> None:
        assert V2_LIMITS.text_axes == {DISPLAY_TEXT: 4000}

    def test_classic_budgets_the_axes_a_classic_message_actually_has(self) -> None:
        assert CLASSIC_TARGET.capacities == {
            CONTENT_TEXT: 2000,
            EMBED_TEXT: 6000,
            EMBEDS: 10,
            ROWS: 5,
            CONTROLS: 25,
            ATTACHMENTS: 10,
        }

    def test_no_classic_strategy_can_borrow_the_v2_totals(self) -> None:
        assert COMPONENTS not in CLASSIC_TARGET.capacities
        assert DISPLAY_TEXT not in CLASSIC_TARGET.capacities

    def test_row_and_control_shape_is_stated_once_for_both_modes(self) -> None:
        for name in ("row_buttons", "button_label", "select_options", "custom_id", "modal_components"):
            assert getattr(CLASSIC_LIMITS, name) == getattr(V2_LIMITS, name)


class TestClassicShape:
    def test_a_card_becomes_one_embed_with_every_slot_it_was_given(self) -> None:
        message = body(
            Card(
                title="Piston door",
                url="https://example.invalid/door",
                children=(Text("first block"), Text("second block")),
                fields=(CardField("Width", "2", inline=True),),
                footer=CardFooter("by squid", "https://example.invalid/f.png"),
                author=CardAuthor("Redstone Squid", "https://example.invalid"),
                accent=0x00FF00,
                image=CardMedia("https://example.invalid/i.png", "the door"),
                thumbnail=CardMedia("https://example.invalid/t.png"),
            )
        )
        embed = message.embeds[0]

        assert embed.title == "Piston door"
        assert embed.description == "first block\n\nsecond block"
        assert embed.fields == (SceneEmbedField("Width", "2", inline=True),)
        assert embed.footer is not None and embed.footer.text == "by squid"
        assert embed.author is not None and embed.author.name == "Redstone Squid"
        assert embed.colour == 0x00FF00
        assert embed.image is not None and embed.image.description == "the door"

    def test_description_blocks_join_deterministically(self) -> None:
        """One rule, so one card always produces one string and one fingerprint."""
        message = body(Card(children=(Text("a"), Text("b"), Text("c"))))

        assert message.embeds[0].description == "a\n\nb\n\nc"

    def test_content_becomes_the_message_content_field(self) -> None:
        assert body(Content("@here ready")).content == "@here ready"

    def test_a_document_may_carry_only_one_content_field(self) -> None:
        with pytest.raises(LayoutInvariantError, match="only one Content node is legal"):
            plan([Content("a"), Content("b")], target=CLASSIC_TARGET)

    def test_rows_become_classic_rows(self) -> None:
        message = body(Row((LinkButton("Docs", "https://example.invalid"),)))

        assert len(message.rows) == 1
        assert message.rows[0].controls[0].url == "https://example.invalid"  # type: ignore[union-attr]

    def test_embed_text_is_stripped_because_discord_trims_it_server_side(self) -> None:
        assert body(Card(title="  spaced  ")).embeds[0].title == "spaced"

    def test_an_empty_optional_value_is_omitted_rather_than_sent_blank(self) -> None:
        assert body(Card(title="   ", children=(Text("body"),))).embeds[0].title is None

    def test_a_field_that_trims_to_nothing_is_rejected_rather_than_dropped(self) -> None:
        with pytest.raises(LayoutInvariantError, match="non-empty name and value"):
            plan(Card(fields=(CardField("  ", "value"),)), target=CLASSIC_TARGET)


class TestGating:
    def test_a_v2_container_has_no_classic_form_and_says_so(self) -> None:
        for node in (Panel(children=(Text("x"),)), Gallery(("https://example.invalid/a.png",))):
            with pytest.raises(LayoutInvariantError, match="no classic form"):
                plan(node, target=CLASSIC_TARGET)  # pyrefly: ignore[bad-argument-type]

    def test_a_section_is_never_silently_reinterpreted_as_a_card(self) -> None:
        section = Section((Text("x"),), accessory=Thumbnail("https://example.invalid/a.png"))

        with pytest.raises(LayoutInvariantError, match="no classic form"):
            plan(section, target=CLASSIC_TARGET)  # pyrefly: ignore[bad-argument-type]


class TestLocalCaps:
    def test_a_direct_value_over_its_cap_is_a_planning_error(self) -> None:
        """Silently clipping a title the author wrote whole is worse than refusing it."""
        with pytest.raises(LayoutInvariantError, match="embed title is 300 characters; the cap is 256"):
            plan(Card(title="x" * 300), target=CLASSIC_TARGET)

    def test_an_explicit_policy_makes_the_same_value_legal(self) -> None:
        title = body(Card(title=Text("x" * 300, overflow=Truncate()))).embeds[0].title

        assert title is not None
        assert len(title) == CLASSIC_LIMITS.embed_title

    def test_a_field_value_over_its_cap_names_the_field_and_the_cap(self) -> None:
        with pytest.raises(LayoutInvariantError, match="field value is 2000 characters; the cap is 1024"):
            plan(Card(fields=(CardField("n", "x" * 2000),)), target=CLASSIC_TARGET)

    def test_more_than_twenty_five_fields_is_refused_at_the_card(self) -> None:
        fields = tuple(CardField(f"n{index}", "v") for index in range(26))

        with pytest.raises(LayoutInvariantError, match="card has 26 fields; the cap is 25"):
            plan(Card(fields=fields), target=CLASSIC_TARGET)


class TestIndependentTextPools:
    def test_filling_the_embed_pool_leaves_content_untouched(self) -> None:
        """The two pools are separate message fields; Discord charges them separately."""
        cards = [Card(children=(Text("x" * 1500),)) for _ in range(4)]
        message = body([Content("@here " + "y" * 1900), *cards])

        assert message.content is not None
        assert len(message.content) == 1906  # every character survived
        assert sum(len(embed.description or "") for embed in message.embeds) <= CLASSIC_LIMITS.embed_text

    def test_filling_the_content_pool_leaves_embed_text_untouched(self) -> None:
        message = body([Content("y" * 4000, overflow=Truncate()), Card(children=(Text("z" * 500),))])

        assert message.content is not None
        assert len(message.content) == CLASSIC_LIMITS.content
        assert message.embeds[0].description == "z" * 500

    def test_content_that_cannot_shrink_and_does_not_fit_is_a_failure(self) -> None:
        with pytest.raises(UnsolvableLayoutError, match="Never nodes need"):
            plan(Content("y" * 3000), target=CLASSIC_TARGET)

    def test_the_pools_do_not_lend_to_each_other(self) -> None:
        """An empty content field does not buy an embed a single extra character."""
        overlong = Card(children=(Text("x" * 6100, overflow=Never()),))

        with pytest.raises(UnsolvableLayoutError, match="Never nodes need"):
            plan(overlong, target=CLASSIC_TARGET)


class TestPagedRegions:
    def test_a_paged_region_plans_against_the_classic_component_budget(self) -> None:
        """`sl.paged` is not mode-gated, so the region breaker may not read a V2-only cap."""
        document = sl.paged(
            sl.group(*(sl.paragraph(f"{index}: " + "x" * 10) for index in range(6))),
            key="report",
            chars=200,
        )

        assert isinstance(plan(document, target=CLASSIC_TARGET).scene.body, SceneClassicMessage)


class TestDiscordPyCrossChecks:
    """Pin what discord.py 2.7.1 actually enforces, against what the limits table claims."""

    def test_discordpy_enforces_the_ten_embed_cap_and_nothing_finer(self) -> None:
        from discord.http import handle_message_parameters

        embeds = [discord.Embed(description="x") for _ in range(CLASSIC_LIMITS.embeds + 1)]

        with pytest.raises(ValueError, match="embeds"):
            handle_message_parameters(embeds=embeds)

    def test_discordpy_enforces_the_twenty_five_view_child_cap(self) -> None:
        view = discord.ui.View(timeout=None)
        for index in range(CLASSIC_LIMITS.controls):
            view.add_item(discord.ui.Button(label="x", custom_id=str(index), row=index // 5))

        with pytest.raises(ValueError, match="maximum number of children"):
            view.add_item(discord.ui.Button(label="over", custom_id="over"))

    def test_an_action_row_only_layout_view_still_sets_the_components_v2_flag(self) -> None:
        """The reason the classic renderer must build a real `View`.

        `ActionRow._is_v2()` returns True by deliberate upstream design, so a LayoutView whose
        payload is identical to a classic one still flags the message irreversibly V2. This is
        the upstream assumption most likely to change.
        """
        layout = discord.ui.LayoutView(timeout=None)
        layout.add_item(discord.ui.ActionRow(discord.ui.Button(label="x", custom_id="x")))

        assert discord.ui.ActionRow()._is_v2() is True
        assert layout.has_components_v2() is True

    def test_a_plain_view_of_the_same_controls_does_not(self) -> None:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="x", custom_id="x"))

        assert view.has_components_v2() is False

    def test_discordpy_rejects_an_action_row_inside_a_plain_view(self) -> None:
        """So rows become `row=` indices on bare items, not `ActionRow` children."""
        with pytest.raises(ValueError, match="v2 items cannot be added to this view"):
            discord.ui.View(timeout=None).add_item(discord.ui.ActionRow())  # type: ignore[arg-type]

    def test_discordpy_validates_no_embed_string_length_locally(self) -> None:
        """Every per-value cap is server-only, which is why Squid audits the payload itself."""
        embed = discord.Embed(title="x" * 500, description="y" * 5000)

        assert len(embed.to_dict()["title"]) == 500

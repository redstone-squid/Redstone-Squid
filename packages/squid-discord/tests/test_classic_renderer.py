"""Drawing a classic message, and the audit that proves the payload before it is sent."""

import re

import discord
import pytest

from squid_discord import CLASSIC_TARGET, classic
from squid_discord.classic_renderer import ClassicRenderer, audit_classic_payload
from squid_layouts.errors import DrawInvariantError
from squid_layouts.planning.limits import CLASSIC_LIMITS
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import (
    SceneButton,
    SceneClassicMessage,
    SceneClassicRow,
    SceneComponentsV2,
    SceneDocument,
    SceneEmbed,
    SceneEmbedField,
    SceneLink,
    SceneOption,
    SceneSelect,
    SceneText,
)
from squid_layouts.semantic import Actions, Link, Note, Paragraph


def scene(body) -> SceneDocument:
    return SceneDocument(SceneCodec.protocol, "discord.components-v1", 1, body)


def link(label: str = "Docs") -> SceneLink:
    return SceneLink(label, "https://example.invalid")


class TestDrawing:
    def test_a_classic_document_draws_the_whole_message(self) -> None:
        presentation = classic.render_static([Paragraph("A 2x2 flush door."), Note("by squid")])

        assert presentation.content is None
        assert presentation.embeds[0].description == "A 2x2 flush door."
        assert presentation.embeds[0].footer.text == "by squid"

    def test_controls_become_a_real_view_with_explicit_rows(self) -> None:
        """Not a `LayoutView`: an ActionRow-only one would still flag the message V2."""
        presentation = classic.render_static(
            [Paragraph("hi"), Actions((Link("d", "Docs", "https://example.invalid"),), key="k")]
        )
        view = presentation.view

        assert isinstance(view, discord.ui.View)
        assert not isinstance(view, discord.ui.LayoutView)
        assert view.has_components_v2() is False
        assert [item.row for item in view.children] == [0]

    def test_a_document_with_no_controls_carries_no_view_at_all(self) -> None:
        assert classic.render_static(Paragraph("hi")).view is None

    def test_each_planned_row_becomes_its_own_row_index(self) -> None:
        body = SceneClassicMessage(
            rows=(
                SceneClassicRow((link("a"), link("b"))),
                SceneClassicRow((link("c"),)),
            )
        )

        view = ClassicRenderer().draw(scene(body)).view

        assert view is not None
        assert [item.row for item in view.children] == [0, 0, 1]


class TestMalformedScenes:
    def test_a_row_mixing_a_select_with_buttons_is_refused(self) -> None:
        body = SceneClassicMessage(rows=(SceneClassicRow((SceneSelect((SceneOption("a", "a"),), "act"), link())),))

        with pytest.raises(DrawInvariantError, match="mixes a select with other controls"):
            ClassicRenderer().draw(scene(body))

    def test_more_than_five_buttons_in_a_row_is_refused(self) -> None:
        body = SceneClassicMessage(rows=(SceneClassicRow(tuple(link(f"b{index}") for index in range(6))),))

        with pytest.raises(DrawInvariantError, match="holds 6 buttons"):
            ClassicRenderer().draw(scene(body))

    def test_an_empty_row_is_refused_because_planning_should_not_emit_one(self) -> None:
        with pytest.raises(DrawInvariantError, match="is empty"):
            ClassicRenderer().draw(scene(SceneClassicMessage(rows=(SceneClassicRow(()),))))

    def test_a_components_v2_body_is_refused_by_name(self) -> None:
        wrong = SceneDocument(SceneCodec.protocol, "discord.components-v1", 1, SceneComponentsV2((SceneText("x"),)))

        with pytest.raises(DrawInvariantError, match="cannot draw a SceneComponentsV2 body"):
            ClassicRenderer().draw(wrong)

    def test_the_v2_target_id_is_refused(self) -> None:
        wrong = SceneDocument(SceneCodec.protocol, "discord.components-v2", 1, SceneClassicMessage())

        with pytest.raises(DrawInvariantError, match=re.escape("cannot draw target 'discord.components-v2'")):
            ClassicRenderer().draw(wrong)

    def test_an_interactive_control_needs_a_mounted_frontend(self) -> None:
        body = SceneClassicMessage(rows=(SceneClassicRow((SceneButton("Save", "save"),)),))

        with pytest.raises(TypeError, match="mounted Discord frontend"):
            ClassicRenderer().draw(scene(body))


class TestPayloadAudit:
    """discord.py accepts all of these locally. Discord answers them with an unhelpful 400."""

    def test_the_aggregate_overrun_discordpy_accepts_is_caught(self) -> None:
        embeds = [discord.Embed(description="x" * 1500) for _ in range(5)]

        assert all(len(embed) == 1500 for embed in embeds)  # discord.py is perfectly happy
        problems = audit_classic_payload(content=None, embeds=embeds, view=None)

        assert any("embed text totals 7500 characters" in problem for problem in problems)

    def test_a_per_value_overrun_is_caught_with_its_cap_named(self) -> None:
        embed = discord.Embed(title="x" * 300, description="y" * 5000)
        embed.add_field(name="n" * 300, value="v" * 2000)

        problems = audit_classic_payload(content=None, embeds=[embed], view=None)

        assert any("title is 300 characters; the limit is 256" in problem for problem in problems)
        assert any("description is 5000 characters; the limit is 4096" in problem for problem in problems)
        assert any("field 0 name is 300 characters; the limit is 256" in problem for problem in problems)
        assert any("field 0 value is 2000 characters; the limit is 1024" in problem for problem in problems)

    def test_duplicate_embed_urls_are_refused_because_discord_hides_the_second(self) -> None:
        """Silently invisible is worse than an error, so this is made into an error."""
        embeds = [discord.Embed(url="https://example.invalid/a", description="x") for _ in range(2)]

        problems = audit_classic_payload(content=None, embeds=embeds, view=None)

        assert any("repeats the URL" in problem for problem in problems)

    def test_distinct_urls_and_a_single_null_url_are_fine(self) -> None:
        embeds = [
            discord.Embed(url="https://example.invalid/a", description="x"),
            discord.Embed(url="https://example.invalid/b", description="y"),
            discord.Embed(description="z"),
            discord.Embed(description="w"),
        ]

        assert audit_classic_payload(content=None, embeds=embeds, view=None) == []

    def test_an_unsupported_url_scheme_is_refused(self) -> None:
        embed = discord.Embed(description="x")
        embed.set_image(url="javascript:alert(1)")

        problems = audit_classic_payload(content=None, embeds=[embed], view=None)

        assert any("unsupported URL scheme 'javascript'" in problem for problem in problems)

    def test_an_attachment_url_is_supported(self) -> None:
        embed = discord.Embed(description="x")
        embed.set_image(url="attachment://door.png")

        assert audit_classic_payload(content=None, embeds=[embed], view=None) == []

    def test_too_many_embeds_are_caught(self) -> None:
        embeds = [discord.Embed(description="x") for _ in range(11)]

        assert any(
            "11 embeds exceed 10" in problem
            for problem in audit_classic_payload(content=None, embeds=embeds, view=None)
        )

    def test_overlong_content_is_caught(self) -> None:
        problems = audit_classic_payload(content="x" * 2001, embeds=[], view=None)

        assert any("content is 2001 characters" in problem for problem in problems)

    def test_duplicate_custom_ids_in_one_message_are_caught(self) -> None:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="a", custom_id="same", row=0))
        view.add_item(discord.ui.Button(label="b", custom_id="same", row=1))

        problems = audit_classic_payload(content=None, embeds=[], view=view)

        assert any("appears twice" in problem for problem in problems)

    def test_discordpy_itself_refuses_a_sixth_row(self) -> None:
        """One of the very few things it does check, so the audit never sees this case."""
        with pytest.raises(ValueError, match="greater than or equal to 5"):
            discord.ui.Button(label="over", custom_id="over", row=5)

    def test_too_many_attachments_are_caught(self) -> None:
        problems = audit_classic_payload(content=None, embeds=[], view=None, attachments=11)

        assert any("11 attachments exceed 10" in problem for problem in problems)

    def test_a_clean_payload_reports_nothing(self) -> None:
        embed = discord.Embed(title="t", description="d")
        embed.add_field(name="n", value="v")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="a", custom_id="a", row=0))

        assert audit_classic_payload(content="hi", embeds=[embed], view=view, attachments=1) == []

    def test_the_aggregate_uses_discord_pys_own_definition(self) -> None:
        """`Embed.__len__` already computes exactly Discord's sum, so this must not re-derive it."""
        embed = discord.Embed(title="tt", description="ddd")
        embed.add_field(name="nn", value="vvvv")
        embed.set_footer(text="ffff")
        embed.set_author(name="aaa")

        assert len(embed) == 2 + 3 + 2 + 4 + 4 + 3


class TestRendererAudit:
    def test_the_renderer_refuses_to_return_an_illegal_payload(self) -> None:
        body = SceneClassicMessage(embeds=tuple(SceneEmbed(description="x" * 1500) for _ in range(5)))

        with pytest.raises(DrawInvariantError, match="embed text totals 7500"):
            ClassicRenderer().draw(scene(body))

    def test_the_audit_can_be_turned_off_for_a_caller_that_owns_the_check(self) -> None:
        body = SceneClassicMessage(embeds=tuple(SceneEmbed(description="x" * 1500) for _ in range(5)))

        presentation = ClassicRenderer(audit=False).draw(scene(body))

        assert len(presentation.embeds) == 5

    def test_a_field_survives_the_trip_through_discord_py(self) -> None:
        body = SceneClassicMessage(embeds=(SceneEmbed(fields=(SceneEmbedField("Width", "2", inline=True),)),))

        embed = ClassicRenderer().draw(scene(body)).embeds[0]

        assert (embed.fields[0].name, embed.fields[0].value, embed.fields[0].inline) == ("Width", "2", True)


class TestPresentationShape:
    def test_the_classic_presentation_names_content_embeds_and_view_on_the_wire(self) -> None:
        presentation = classic.render_static(Paragraph("hi"))

        assert set(presentation._send_fields()) == {"content", "embeds", "view"}

    def test_the_limits_come_from_the_target(self) -> None:
        assert ClassicRenderer().limits is CLASSIC_LIMITS
        assert CLASSIC_TARGET.limits is CLASSIC_LIMITS

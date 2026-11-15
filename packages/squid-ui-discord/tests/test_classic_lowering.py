"""Semantic nodes lowered to classic message content, embeds, and controls."""

from squid_ui import Tone, scene
from squid_ui.authoring.document import Asset, InlineAsset
from squid_ui.authoring.semantic import (
    Article,
    Aside,
    Column,
    Columns,
    Download,
    Field,
    Fields,
    Figure,
    Heading,
    MediaItem,
    Note,
    Paragraph,
    Table,
    TableRow,
)
from squid_ui.ext.discord import DISCORD_V1_DPY27
from squid_ui.planning import plan


def message(document, **kwargs) -> scene.ClassicMessage:
    body = plan(document, target=DISCORD_V1_DPY27, **kwargs).scene.body
    assert isinstance(body, scene.ClassicMessage)
    return body


def codes(document) -> list[str]:
    return [event.code for event in plan(document, target=DISCORD_V1_DPY27).report.events]


class TestLooseProse:
    def test_consecutive_prose_uses_message_content(self) -> None:
        body = message([Paragraph("first"), Paragraph("second")])

        assert body.embeds == ()
        assert body.content == "first\nsecond"

    def test_loose_headings_remain_in_message_content(self) -> None:
        body = message([Heading("Piston door"), Paragraph("body")])

        assert body.content == "## Piston door\nbody"
        assert body.embeds == ()

    def test_loose_headings_preserve_author_order(self) -> None:
        body = message([Heading("One"), Paragraph("a"), Heading("Two"), Paragraph("b")])

        assert body.embeds == ()
        assert body.content == "## One\na\n## Two\nb"


class TestRegions:
    def test_an_article_becomes_one_card_carrying_every_slot_it_names(self) -> None:
        body = message(
            Article(
                Heading("Door"),
                (Paragraph("body"), Fields((Field("w", "Width", "2"),))),
                thumbnail="https://example.invalid/t.png",
            )
        )

        assert len(body.embeds) == 1
        embed = body.embeds[0]
        assert (embed.title, embed.description) == ("Door", "body")
        assert embed.fields[0].name == "Width"
        assert embed.thumbnail is not None

    def test_an_aside_carries_its_tone_as_the_embed_colour(self) -> None:
        body = message(Aside((Paragraph("careful"),), tone=Tone.WARNING))

        assert body.embeds[0].colour is not None

    def test_two_adjacent_regions_stay_two_cards(self) -> None:
        """Merging them would change the author's grouping rather than express it."""
        body = message([Article(Heading("A"), (Paragraph("one"),)), Article(Heading("B"), (Paragraph("two"),))])

        assert [embed.title for embed in body.embeds] == ["A", "B"]


class TestNotes:
    def test_a_trailing_note_remains_in_content(self) -> None:
        body = message([Paragraph("body"), Note("submitted by squid")])

        assert body.content == "body\n-# submitted by squid"

    def test_multiple_notes_retain_their_order(self) -> None:
        body = message([Paragraph("body"), Note("first"), Note("second")])

        assert body.content == "body\n-# first\n-# second"


class TestFields:
    def test_fields_become_real_embed_fields(self) -> None:
        body = message(Fields((Field("w", "Width", "2"), Field("h", "Height", "3"))))

        assert [(field.name, field.value) for field in body.embeds[0].fields] == [("Width", "2"), ("Height", "3")]

    def test_more_than_twenty_five_fields_continue_into_another_card(self) -> None:
        """Lossless: every field is still shown, in order, on a second embed."""
        body = message(Fields(tuple(Field(f"k{index}", f"n{index}", "v") for index in range(30))))

        assert [len(embed.fields) for embed in body.embeds] == [25, 5]
        assert body.embeds[1].fields[-1].name == "n29"

    def test_the_exact_field_form_is_chosen_while_it_fits(self) -> None:
        """The description-line rung exists, but it reformats, so it loses to the exact one."""
        document = Fields((Field("w", "Width", "2"),))

        assert codes(document) == []
        assert message(document).embeds[0].fields

    def test_a_field_ladder_survives_as_the_values_overflow_policy(self) -> None:
        # Each field fits its slot, but together they exceed the shared embed budget.
        long = "x" * 900
        body = message(Fields(tuple(Field(str(index), "Links", long, fallbacks=("short",)) for index in range(7))))

        values = [field.value for embed in body.embeds for field in embed.fields]
        assert len(values) == 7
        assert set(values) <= {long, "short"}
        assert "short" in values


class TestTables:
    def test_a_table_preserves_column_names_beside_values(self) -> None:
        body = message(
            Table(
                Columns((Column("a", "Name"), Column("b", "Size"))),
                (TableRow("r", ("door", "2x2")),),
                key="t",
            )
        )

        assert body.content == "Name: door\nSize: 2x2"


class TestMedia:
    def test_a_figure_uses_the_image_slot_and_keeps_its_description(self) -> None:
        body = message(Figure(MediaItem("i", "https://example.invalid/i.png", description="the door")))

        assert body.embeds[0].image is not None
        assert body.embeds[0].image.description == "the door"

    def test_a_figure_caption_stays_visible(self) -> None:
        body = message(Figure(MediaItem("i", "https://example.invalid/i.png"), caption="Fig 1"))

        assert "Fig 1" in (body.content or "")


class TestDownloads:
    def test_the_asset_still_uploads_and_the_label_still_shows(self) -> None:
        document = Download("dl", "Schematic", Asset("s", "s.litematic", "application/octet-stream", InlineAsset(b"x")))
        body = message(document)

        assert body.content == "Schematic"

    def test_download_retains_its_delivery_resource(self) -> None:
        document = Download("dl", "Schematic", Asset("s", "s.litematic", "application/octet-stream", InlineAsset(b"x")))

        result = plan(document, target=DISCORD_V1_DPY27)
        assert result.resources["asset:s"] is document.asset
        assert result.scene.assets[0].name == "s.litematic"

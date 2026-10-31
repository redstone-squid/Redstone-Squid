"""How each semantic node reaches a classic message. One test per row of the mapping."""

from squid_ui import Tone, scene
from squid_ui.document import Asset, InlineAsset
from squid_ui.planning import plan
from squid_ui.semantic import (
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
from squid_ui_discord import DISCORD_V1_DPY27


def message(document, **kwargs) -> scene.ClassicMessage:
    body = plan(document, target=DISCORD_V1_DPY27, **kwargs).scene.body
    assert isinstance(body, scene.ClassicMessage)
    return body


def codes(document) -> list[str]:
    return [event.code for event in plan(document, target=DISCORD_V1_DPY27).report.events]


class TestLooseProse:
    def test_consecutive_prose_becomes_one_implicit_card(self) -> None:
        body = message([Paragraph("first"), Paragraph("second")])

        assert len(body.embeds) == 1
        assert body.embeds[0].description == "first\n\nsecond"

    def test_the_first_suitable_heading_becomes_the_title(self) -> None:
        body = message([Heading("Piston door"), Paragraph("body")])

        assert body.embeds[0].title == "Piston door"
        assert body.embeds[0].description == "body"

    def test_a_later_heading_stays_formatted_description_text(self) -> None:
        """An embed has one title; inventing a card for the second heading would regroup
        the document rather than express it."""
        body = message([Heading("One"), Paragraph("a"), Heading("Two"), Paragraph("b")])

        assert len(body.embeds) == 1
        assert body.embeds[0].title == "One"
        assert "## Two" in (body.embeds[0].description or "")


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
    def test_a_single_trailing_note_takes_the_footer_slot(self) -> None:
        body = message([Paragraph("body"), Note("submitted by squid")])

        assert body.embeds[0].footer is not None
        assert body.embeds[0].footer.text == "submitted by squid"

    def test_a_second_note_stays_subtle_description_text(self) -> None:
        """An embed has exactly one footer, so the second one cannot have that slot."""
        body = message([Paragraph("body"), Note("first"), Note("second")])

        footers = [embed.footer.text for embed in body.embeds if embed.footer is not None]
        assert footers == ["first"]
        assert any("second" in (embed.description or "") for embed in body.embeds)


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
        long = "x" * 900
        body = message(Fields((Field("k", "Links", long, fallbacks=("short",)),)))

        assert body.embeds[0].fields[0].value in {long, "short"}


class TestTables:
    def test_a_table_stays_an_aligned_code_block_while_it_fits(self) -> None:
        body = message(
            Table(
                Columns((Column("a", "Name"), Column("b", "Size"))),
                (TableRow("r", ("door", "2x2")),),
                key="t",
            )
        )

        assert "```" in (body.embeds[0].description or "")


class TestMedia:
    def test_a_figure_uses_the_image_slot_and_keeps_its_description(self) -> None:
        body = message(Figure(MediaItem("i", "https://example.invalid/i.png", description="the door")))

        assert body.embeds[0].image is not None
        assert body.embeds[0].image.description == "the door"

    def test_a_figure_caption_takes_the_footer(self) -> None:
        body = message(Figure(MediaItem("i", "https://example.invalid/i.png"), caption="Fig 1"))

        assert body.embeds[0].footer is not None
        assert body.embeds[0].footer.text == "Fig 1"


class TestDownloads:
    def test_the_asset_still_uploads_and_the_label_still_shows(self) -> None:
        document = Download("dl", "Schematic", Asset("s", "s.litematic", "application/octet-stream", InlineAsset(b"x")))
        body = message(document)

        assert "Schematic" in (body.embeds[0].description or "")

    def test_losing_the_file_component_is_reported_rather_than_left_to_be_noticed(self) -> None:
        document = Download("dl", "Schematic", Asset("s", "s.litematic", "application/octet-stream", InlineAsset(b"x")))

        assert "download.attachment_only" in codes(document)

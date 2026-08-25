"""Cross-check the limits table against what discord.py 2.7 enforces locally.

discord.py validates child counts at mutation time but no string lengths. If one of these
starts failing after a discord.py upgrade, the limits table (or the conform gate's division of
labor) needs a fresh look.
"""

import discord
import pytest

from squid_discord import V2_LIMITS as LIMITS


def test_view_child_count_matches_discordpy():
    view = discord.ui.LayoutView()
    for _ in range(LIMITS.total_components):
        view.add_item(discord.ui.TextDisplay("x"))
    with pytest.raises(ValueError, match="maximum number of children"):
        view.add_item(discord.ui.TextDisplay("x"))


def test_section_text_count_matches_discordpy():
    section = discord.ui.Section(accessory=discord.ui.Thumbnail("https://example.invalid/a.png"))
    for _ in range(LIMITS.section_texts):
        section.add_item(discord.ui.TextDisplay("x"))
    with pytest.raises(ValueError, match="maximum number of children"):
        section.add_item(discord.ui.TextDisplay("x"))


def test_modal_child_count_matches_discordpy():
    modal = discord.ui.Modal(title="t", timeout=None)
    for index in range(LIMITS.modal_components):
        modal.add_item(discord.ui.Label(text=f"l{index}", component=discord.ui.TextInput(label="i")))
    with pytest.raises(ValueError, match="maximum number of children"):
        modal.add_item(discord.ui.Label(text="over", component=discord.ui.TextInput(label="i")))


def test_nested_children_count_against_the_same_view_total():
    view = discord.ui.LayoutView()
    container = discord.ui.Container()
    view.add_item(container)
    with pytest.raises(ValueError, match="maximum number of children"):
        for _ in range(LIMITS.total_components):
            container.add_item(discord.ui.TextDisplay("x"))


def test_gallery_item_count_matches_discordpy():
    gallery = discord.ui.MediaGallery()
    with pytest.raises(ValueError, match="up to 10 items"):
        gallery.items = [
            discord.MediaGalleryItem("https://example.invalid/a.png") for _ in range(LIMITS.gallery_items + 1)
        ]


def test_select_option_count_matches_discordpy():
    select = discord.ui.Select()
    for index in range(LIMITS.select_options):
        select.append_option(discord.SelectOption(label=str(index)))
    with pytest.raises(ValueError, match="maximum number of options"):
        select.append_option(discord.SelectOption(label="over"))


def test_discordpy_does_not_validate_string_lengths():
    # The premise of the conform gate: these all serialize locally and would 50035 at send time.
    modal = discord.ui.Modal(title="t" * (LIMITS.modal_title + 1), timeout=None)
    modal.add_item(discord.ui.Label(text="l", component=discord.ui.TextInput(label="i", default="v" * 5000)))
    payload = modal.to_dict()
    assert len(payload["title"]) > LIMITS.modal_title
    assert len(payload["components"][0]["component"]["value"]) > LIMITS.text_input_value

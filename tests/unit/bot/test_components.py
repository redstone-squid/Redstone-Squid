"""Components V2 rendering boundary tests."""

from typing import cast

import discord
import pytest

from squid.bot.utils.components import (
    CardField,
    card_layout,
    edit_layout,
    link_layout,
    text_layout,
    truncate_display_text,
)
from tests.helpers.discord import make_message


def test_card_layout_serializes_as_components_v2() -> None:
    layout = card_layout(
        "Status",
        "Ready",
        fields=(CardField("Count", "3"),),
        footer="Updated now",
    )

    payload = layout.to_components()
    children = payload[0]["components"]

    assert layout.has_components_v2()
    assert payload[0]["type"] == discord.ComponentType.container.value
    # Title and body are separate TextDisplays so the body can trim independently.
    assert children[0]["content"] == "## Status"
    assert children[1]["content"] == "Ready"
    assert children[3]["content"] == "**Count**\n3"
    assert children[4]["content"] == "-# Updated now"


def test_link_layout_uses_a_link_button() -> None:
    payload = link_layout("Documentation", "https://example.com", label="Read").to_components()

    assert "https://example.com" in str(payload)
    assert "'style': 5" in str(payload)


def test_text_layout_truncates_to_the_v2_display_limit() -> None:
    layout = text_layout("x" * 5000)

    assert layout.content_length() == 4000
    assert truncate_display_text("abcd", 3) == "ab…"


@pytest.mark.asyncio
async def test_edit_layout_clears_legacy_fields_when_converting() -> None:
    harness = make_message()
    layout = text_layout("Converted")

    await edit_layout(harness.message, layout)

    call = harness.edit.await_args
    assert call is not None
    assert call.kwargs["content"] is None
    assert call.kwargs["embed"] is None
    assert call.kwargs["view"] is layout


@pytest.mark.asyncio
async def test_edit_layout_does_not_resend_legacy_fields_for_v2_message() -> None:
    harness = make_message(components_v2=True)
    layout = text_layout("Updated")

    result = await edit_layout(harness.message, layout)

    assert result is cast(discord.Message, harness.edit.return_value)
    call = harness.edit.await_args
    assert call is not None
    assert "content" not in call.kwargs
    assert "embed" not in call.kwargs

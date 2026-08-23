"""Components V2 rendering boundary tests."""

import discord
import pytest

import squid_layouts as sl
from squid.bot.ui import (
    CardField,
    L,
    card_layout,
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

    payload = layout.layout.to_components()
    children = payload[0]["components"]

    assert layout.layout.has_components_v2()
    assert payload[0]["type"] == discord.ComponentType.container.value
    # Title and body are separate TextDisplays so the body can trim independently.
    assert children[0]["content"] == "## Status"
    assert children[1]["content"] == "Ready"
    # sl.fields() renders "**label:** value" on one line, not presets.card's two-line form.
    assert children[2]["content"] == "**Count:** 3"
    assert children[3]["content"] == "-# Updated now"


def test_link_layout_uses_a_link_button() -> None:
    payload = link_layout("Documentation", "https://example.com", label="Read").layout.to_components()

    assert "https://example.com" in str(payload)
    assert "'style': 5" in str(payload)


def test_deferred_template_marker_preserves_msgid_and_values() -> None:
    page = 3
    pages = 7

    message = L(t"Page {page} of {pages}")

    assert message.template == "Page {page} of {pages}"
    assert message.params == {"page": 3, "pages": 7}


def test_deferred_template_marker_rejects_expression_placeholders() -> None:
    page = 3

    with pytest.raises(ValueError, match="placeholder name"):
        L(t"Page {page + 1}")


def test_text_layout_truncates_to_the_v2_display_limit() -> None:
    layout = text_layout("x" * 5000)

    assert layout.layout.content_length() == 4000
    assert truncate_display_text("abcd", 3) == "ab…"


@pytest.mark.asyncio
async def test_delivery_clears_legacy_fields_when_converting() -> None:
    harness = make_message()
    layout = text_layout("Converted")

    await sl.discord.delivery.handle_for(
        harness.message,
        mode=sl.discord.presentation.DiscordMode.CLASSIC,
    ).write(layout)

    call = harness.edit.await_args
    assert call is not None
    assert call.kwargs["content"] is None
    assert call.kwargs["embeds"] == []
    assert call.kwargs["view"] is layout.layout


@pytest.mark.asyncio
async def test_delivery_does_not_resend_legacy_fields_for_v2_message() -> None:
    harness = make_message(components_v2=True)
    layout = text_layout("Updated")

    await sl.discord.delivery.handle_for(harness.message, mode=layout.mode).write(layout)

    call = harness.edit.await_args
    assert call is not None
    assert "content" not in call.kwargs
    assert "embeds" not in call.kwargs

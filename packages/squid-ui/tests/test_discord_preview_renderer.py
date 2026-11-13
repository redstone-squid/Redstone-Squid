"""Structure, escaping, and codec round trips for the Discord Components V2 HTML preview."""

from dataclasses import replace
from typing import cast

import pytest

from squid_ui import scene
from squid_ui.entity import EntityKind, EntityRef, EntityType
from squid_ui.errors import DrawInvariantError
from squid_ui.html import DiscordPreviewRenderer
from squid_ui.interactions import ActionMode
from squid_ui.text import Markup


def _scene() -> scene.Scene[scene.ComponentsV2]:
    return scene.Scene(
        protocol=scene.Codec.protocol,
        target="discord.components-v2",
        target_version=1,
        body=scene.ComponentsV2(
            (
                scene.Panel(
                    (
                        scene.Text("<script>alert(1)</script>"),
                        scene.Time("2026-08-22T14:30:00+00:00", "R", "Updated: "),
                        scene.ZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "Starts: "),
                        scene.Row((scene.Button("Save", "form.save", mode=ActionMode.EXCLUSIVE),)),
                        scene.Select((scene.Option("One", "1"),), "form.choice"),
                        scene.Gallery((scene.GalleryItem("https://example.invalid/image.png", "preview"),)),
                    ),
                    accent=0x5865F2,
                ),
            )
        ),
    )


def test_html_renderer_preserves_structure_and_action_ids_without_callbacks() -> None:
    rendered = DiscordPreviewRenderer().draw(_scene())

    assert 'class="squid-panel"' in rendered
    assert 'data-squid-action="form.save"' in rendered
    assert 'data-squid-action="form.choice"' in rendered
    assert 'data-squid-markup="plain"' in rendered
    assert rendered.count('data-squid-mode="exclusive"') == 2
    assert "data-squid-dialect" not in rendered
    assert "data-squid-policy" not in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert '<time datetime="2026-08-22T14:30:00+00:00" data-squid-style="R">' in rendered
    assert (
        '<time datetime="2026-08-22T14:30:00+00:00" data-squid-timezone="America/New_York">'
        "2026-08-22 10:30:00-04:00[America/New_York]</time>"
    ) in rendered


def test_scene_json_can_be_drawn_by_a_separate_frontend_process() -> None:
    document = _scene()
    restored = scene.Codec.loads(scene.Codec.dumps(document))
    restored_v2 = cast(scene.Scene[scene.ComponentsV2], restored)

    assert DiscordPreviewRenderer().draw(restored_v2) == DiscordPreviewRenderer().draw(document)


def test_standalone_preview_includes_discord_like_css() -> None:
    rendered = DiscordPreviewRenderer(standalone=True).draw(_scene())

    assert rendered.startswith("<!doctype html>")
    assert ".squid-panel" in rendered
    assert "background:#313338" in rendered


def test_html_preview_rejects_an_unknown_target_version() -> None:
    with pytest.raises(DrawInvariantError, match=r"target version 99"):
        DiscordPreviewRenderer().draw(replace(_scene(), target_version=99))


def test_entity_select_shows_its_current_values_as_mentions() -> None:
    from_guild = scene.EntitySelect(
        EntityType.CONVERSATION,
        "settings.channel",
        placeholder="Choose a channel",
        default_values=(EntityRef(EntityKind.CONVERSATION, 42),),
    )
    empty = scene.EntitySelect(EntityType.ROLE, "settings.role", placeholder="Choose a role")
    document = replace(_scene(), body=scene.ComponentsV2((from_guild, empty)))

    rendered = DiscordPreviewRenderer().draw(document)

    assert '<option value="42" selected>&lt;#42&gt;</option>' in rendered
    assert '<option value="" disabled>Choose a channel</option>' in rendered
    assert '<option value="" disabled selected>Choose a role</option>' in rendered
    assert 'data-squid-entity="role"' in rendered


def test_preview_renders_markdown_blocks_and_preserves_literal_text() -> None:
    document = replace(
        _scene(),
        body=scene.ComponentsV2(
            (
                scene.Text("# Title\n\n**Ready**\n\n- first\n- second\n\n```\nx < y\n```", Markup.DISCORD_MARKDOWN),
                scene.Text("**literal** <script>", Markup.PLAIN),
            )
        ),
    )

    rendered = DiscordPreviewRenderer().draw(document)

    assert "<h1>Title</h1>" in rendered
    assert "<strong>Ready</strong>" in rendered
    assert "<ul><li>" in rendered
    assert "<pre><code>x &lt; y\n</code></pre>" in rendered
    assert "**literal** &lt;script&gt;" in rendered

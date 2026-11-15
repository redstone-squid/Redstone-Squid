"""Structure, escaping, and codec round trips for the Discord Components V2 HTML preview."""

from dataclasses import replace
from typing import cast

import pytest

from squid_ui import scene
from squid_ui.entity import EntityKind, EntityRef, EntityType
from squid_ui.errors import DrawInvariantError
from squid_ui.interactions import ActionMode
from squid_ui.text import Markup
from squid_ui_discord.preview import DiscordPreviewRenderer


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
    assert "background: #313338" in rendered


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


def test_preview_styles_discord_subtext_and_revealable_spoilers() -> None:
    document = replace(
        _scene(),
        body=scene.ComponentsV2(
            (
                scene.Text("-# Small ||secret||", Markup.DISCORD_MARKDOWN),
                scene.Text("-# ||literal||", Markup.PLAIN),
            )
        ),
    )

    rendered = DiscordPreviewRenderer(standalone=True).draw(document)

    assert '<p class="squid-subtext">Small <label class="squid-text-spoiler">' in rendered
    assert '<input type="checkbox" aria-label="Reveal spoiler">' in rendered
    assert "visibility: hidden" in rendered
    assert "input:checked+.squid-text-spoiler__content{visibility:visible;}" in "".join(rendered.split())
    assert "-# ||literal||" in rendered


@pytest.mark.parametrize("routed", [False, True])
def test_preview_controls_preserve_escaped_binding_and_option_metadata(routed: bool) -> None:
    identifier = 'choose"<&'
    options = (scene.Option("<One>", 'one"&', default=True),)
    button = (
        scene.RoutedButton("<Save>", identifier, disabled=True)
        if routed
        else scene.Button("<Save>", identifier, disabled=True)
    )
    select = (
        scene.RoutedSelect(options, identifier, placeholder="<Choose>", min_values=0, max_values=2, disabled=True)
        if routed
        else scene.Select(options, identifier, placeholder="<Choose>", min_values=0, max_values=2, disabled=True)
    )
    document = replace(_scene(), body=scene.ComponentsV2((scene.Row((button,)), select)))

    rendered = DiscordPreviewRenderer().draw(document)

    binding = "data-route-id" if routed else "data-squid-action"
    assert rendered.count(f'{binding}="choose&quot;&lt;&amp;"') == 2
    assert ('data-squid-mode="exclusive"' in rendered) is not routed
    assert "disabled>&lt;Save&gt;</button>" in rendered
    assert 'data-squid-min="0" data-squid-max="2" multiple disabled' in rendered
    assert '<option value="" disabled selected>&lt;Choose&gt;</option>' in rendered
    assert '<option value="one&quot;&amp;" selected>&lt;One&gt;</option>' in rendered


@pytest.mark.parametrize("url, disabled", [("javascript:alert(1)", False), ("https://example.com", True)])
def test_unavailable_preview_links_have_no_navigation(url: str, disabled: bool) -> None:
    document = replace(_scene(), body=scene.ComponentsV2((scene.Row((scene.Link("<Link>", url, disabled=disabled),)),)))

    rendered = DiscordPreviewRenderer().draw(document)

    assert '<span class="squid-button squid-link" aria-disabled="true">&lt;Link&gt;</span>' in rendered
    assert "href=" not in rendered


def test_spoiler_panel_keeps_focus_and_accent_attributes_separate() -> None:
    document = replace(
        _scene(),
        body=scene.ComponentsV2((scene.Panel((scene.Text("Hidden"),), spoiler=True, accent=0x123456),)),
    )

    rendered = DiscordPreviewRenderer().draw(document)

    assert '<section class="squid-panel squid-spoiler" tabindex="0" style="--squid-accent:#123456">' in rendered

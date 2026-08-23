"""HTML drawing of the same immutable scenes used by Discord."""

from dataclasses import replace

import pytest

from squid_layouts.interactions import ActionPolicy
from squid_layouts.errors import DrawInvariantError
from squid_layouts.html import Renderer
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import (
    SceneButton,
    SceneComponentsV2,
    SceneDocument,
    SceneGallery,
    SceneGalleryItem,
    SceneOption,
    ScenePanel,
    SceneRow,
    SceneSelect,
    SceneText,
    SceneTime,
    SceneZonedTime,
)


def _scene() -> SceneDocument:
    return SceneDocument(
        protocol=SceneCodec.protocol,
        target="discord.components-v2",
        target_version=1,
        body=SceneComponentsV2(
            (
                ScenePanel(
                    (
                        SceneText("<script>alert(1)</script>"),
                        SceneTime("2026-08-22T14:30:00+00:00", "R", "Updated: "),
                        SceneZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "Starts: "),
                        SceneRow((SceneButton("Save", "form.save", policy=ActionPolicy.EXCLUSIVE),)),
                        SceneSelect((SceneOption("One", "1"),), "form.choice"),
                        SceneGallery((SceneGalleryItem("https://example.invalid/image.png", "preview"),)),
                    ),
                    accent=0x5865F2,
                ),
            )
        ),
    )


def test_html_renderer_preserves_structure_and_action_ids_without_callbacks() -> None:
    rendered = Renderer().draw(_scene())

    assert 'class="squid-panel"' in rendered
    assert 'data-squid-action="form.save"' in rendered
    assert 'data-squid-action="form.choice"' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert '<time datetime="2026-08-22T14:30:00+00:00" data-squid-style="R">' in rendered
    assert (
        '<time datetime="2026-08-22T14:30:00+00:00" data-squid-timezone="America/New_York">'
        "2026-08-22 10:30:00-04:00[America/New_York]</time>"
    ) in rendered


def test_scene_json_can_be_drawn_by_a_separate_frontend_process() -> None:
    scene = _scene()
    restored = SceneCodec.loads(SceneCodec.dumps(scene))

    assert Renderer().draw(restored) == Renderer().draw(scene)


def test_standalone_preview_includes_discord_like_css() -> None:
    rendered = Renderer(standalone=True).draw(_scene())

    assert rendered.startswith("<!doctype html>")
    assert ".squid-panel" in rendered
    assert "background:#313338" in rendered


def test_html_preview_rejects_an_unknown_target_version() -> None:
    with pytest.raises(DrawInvariantError, match=r"target .* version 99"):
        Renderer().draw(replace(_scene(), target_version=99))

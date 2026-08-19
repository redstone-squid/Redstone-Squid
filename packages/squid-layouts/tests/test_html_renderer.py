"""HTML drawing of the same immutable scenes used by Discord."""

from dataclasses import replace

import pytest

from squid_layouts.actions import ActionPolicy
from squid_layouts.errors import DrawInvariantError
from squid_layouts.html import HtmlRenderer
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import (
    SceneButton,
    SceneDocument,
    SceneGallery,
    SceneGalleryItem,
    SceneOption,
    ScenePanel,
    SceneRow,
    SceneSelect,
    SceneText,
)


def _scene() -> SceneDocument:
    return SceneDocument(
        protocol=SceneCodec.protocol,
        target="discord.components-v2",
        target_version=1,
        children=(
            ScenePanel(
                (
                    SceneText("<script>alert(1)</script>"),
                    SceneRow((SceneButton("Save", "form.save", policy=ActionPolicy.EXCLUSIVE),)),
                    SceneSelect((SceneOption("One", "1"),), "form.choice"),
                    SceneGallery((SceneGalleryItem("https://example.invalid/image.png", "preview"),)),
                ),
                accent=0x5865F2,
            ),
        ),
    )


def test_html_renderer_preserves_structure_and_action_ids_without_callbacks() -> None:
    rendered = HtmlRenderer().draw(_scene())

    assert 'class="squid-panel"' in rendered
    assert 'data-squid-action="form.save"' in rendered
    assert 'data-squid-action="form.choice"' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_scene_json_can_be_drawn_by_a_separate_frontend_process() -> None:
    scene = _scene()
    restored = SceneCodec.loads(SceneCodec.dumps(scene))

    assert HtmlRenderer().draw(restored) == HtmlRenderer().draw(scene)


def test_standalone_preview_includes_discord_like_css() -> None:
    rendered = HtmlRenderer(standalone=True).draw(_scene())

    assert rendered.startswith("<!doctype html>")
    assert ".squid-panel" in rendered
    assert "background:#313338" in rendered


def test_html_preview_rejects_an_unknown_target_version() -> None:
    with pytest.raises(DrawInvariantError, match=r"target .* version 99"):
        HtmlRenderer().draw(replace(_scene(), target_version=99))

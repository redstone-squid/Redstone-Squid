"""Safe native HTML drawing from the semantic scene protocol."""

from dataclasses import replace
from typing import Any, cast

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.errors import DrawInvariantError
from squid_ui.forms import FormSpec, TextField
from squid_ui.html import DEFAULT_CSS, Renderer
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.text import Markup


async def _pressed(event: ActionEvent) -> None:
    del event


async def _submitted(event: SubmitEvent) -> None:
    del event


def _html_scene(*children: scene.HtmlNode, locale: str | None = "en-GB") -> scene.Scene[scene.HtmlBody]:
    return scene.Scene(
        protocol=scene.Codec.protocol,
        target="html.semantic",
        target_version=1,
        body=scene.HtmlBody(tuple(children), locale),
    )


def test_renderer_draws_native_controls_and_host_dispatch_metadata() -> None:
    form = FormSpec("Edit", (TextField("Name", "name", required=True),), prefill={"name": 'A&B "door"'})
    document = sl.stack(
        sl.action_controls(
            sl.action_control("Save", _pressed, key="save"),
            sl.routed_action_control("Open", "build:1:open", key="open"),
            key="actions",
        ),
        sl.choices(*(sl.choice(str(index), key=str(index)) for index in range(30)), key="choice", maximum=3),
        sl.form("Submit", form, key="edit", on_submit=_submitted),
    )
    result = sl.planning.plan(document, target=sl.html.target(), localization=sl.text.Localization("en-GB"))

    rendered = Renderer().draw(result.scene, plan=result)

    assert rendered.startswith('<main class="squid-document" lang="en-GB"')
    assert 'data-squid-action="save" data-squid-mode="exclusive"' in rendered
    assert 'data-route-id="build:1:open"' in rendered
    assert 'data-squid-action="choice"' in rendered
    assert 'data-squid-min="1" data-squid-max="3"' in rendered
    assert rendered.count("<option") == 30
    assert '<form class="squid-form" data-squid-action="edit"' in rendered
    assert 'name="name" required type="text" value="A&amp;B &quot;door&quot;"' in rendered
    assert 'data-squid-form="edit" data-squid-field="name"' in rendered
    assert "<script" not in rendered


def test_renderer_parses_only_safe_allowlisted_markdown() -> None:
    content = (
        "<script>alert(1)</script> **bold** *em* ~~gone~~ `x<y` "
        "[safe](https://example.invalid/a?q=1&x=2) "
        "![preview](https://example.invalid/image.png) [bad](javascript:alert(1)) "
        "<t:123:R> <:wave:123> ||spoiler||"
    )
    document = _html_scene(scene.HtmlElement(scene.HtmlTag.P, (scene.HtmlText(content, Markup.DISCORD_MARKDOWN),)))

    rendered = Renderer().draw(document)

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<strong>bold</strong>" in rendered
    assert "<em>em</em>" in rendered
    assert "<s>gone</s>" in rendered
    assert "<code>x&lt;y</code>" in rendered
    assert 'href="https://example.invalid/a?q=1&amp;x=2" rel="noopener noreferrer"' in rendered
    assert 'src="https://example.invalid/image.png" alt="preview"' in rendered
    assert 'href="javascript:' not in rendered
    assert "[bad](javascript:alert(1))" in rendered
    assert "&lt;t:123:R&gt; &lt;:wave:123&gt; ||spoiler||" in rendered


def test_renderer_validates_scene_urls_and_escapes_attributes() -> None:
    document = _html_scene(
        scene.HtmlElement(
            scene.HtmlTag.A,
            (scene.HtmlText("unsafe"),),
            attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.TITLE, '"><script>alert(1)</script>'),),
            url=scene.HtmlUrlRef('javascript:alert("x")'),
        ),
        scene.HtmlElement(
            scene.HtmlTag.IMG,
            attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.ALT, '"><img src=x>'),),
            url=scene.HtmlUrlRef("data:text/html,<script>alert(1)</script>"),
        ),
    )

    rendered = Renderer().draw(document)

    assert 'href="javascript:' not in rendered
    assert 'src="data:' not in rendered
    assert rendered.count('aria-disabled="true"') == 1
    assert "<script>" not in rendered
    assert "<img src=x>" not in rendered
    assert "&quot;&gt;&lt;script&gt;" in rendered


def test_renderer_resolves_declared_inline_assets_without_exposing_callbacks() -> None:
    asset = sl.document.Asset(
        "report",
        'report"><script>.txt',
        "text/plain",
        sl.document.InlineAsset(b"hello"),
    )
    result = sl.planning.plan(sl.download(asset, label="Report", key="report"), target=sl.html.target())

    rendered = Renderer().draw(result.scene, plan=result)

    assert 'href="data:text/plain;base64,aGVsbG8="' in rendered
    assert 'download="report&quot;&gt;&lt;script&gt;.txt"' in rendered
    assert "<script>" not in rendered
    assert "_pressed" not in rendered


def test_renderer_emits_only_typed_css_variable_colours() -> None:
    document = _html_scene(
        scene.HtmlElement(scene.HtmlTag.SECTION, colour=scene.HtmlColourRef(0x00A1FF)),
    )

    assert 'style="--squid-accent:#00a1ff"' in Renderer().draw(document)


def test_standalone_renderer_escapes_title_and_sets_locale_viewport_and_neutral_css() -> None:
    document = _html_scene(scene.HtmlElement(scene.HtmlTag.P, (scene.HtmlText("Hello"),)), locale="de-DE")

    rendered = Renderer(standalone=True, title="Builds </title><script>alert(1)</script>").draw(document)

    assert rendered.startswith('<!doctype html><html lang="de-DE">')
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in rendered
    assert "<title>Builds &lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>" in rendered
    assert "color-scheme:light dark" in rendered
    assert DEFAULT_CSS in rendered
    assert rendered.count("<script>") == 0


def test_renderer_rejects_non_html_bodies_versions_and_invalid_reference_placement() -> None:
    document = _html_scene(scene.HtmlElement(scene.HtmlTag.DIV))
    discord_scene = scene.Scene(
        protocol=scene.Codec.protocol,
        target="discord.components-v2",
        target_version=1,
        body=scene.ComponentsV2(),
    )
    misplaced = _html_scene(scene.HtmlElement(scene.HtmlTag.DIV, url=scene.HtmlUrlRef("https://example.invalid")))

    with pytest.raises(DrawInvariantError, match="ComponentsV2"):
        Renderer().draw(cast(Any, discord_scene))
    with pytest.raises(DrawInvariantError, match="version 99"):
        Renderer().draw(replace(document, target_version=99))
    with pytest.raises(DrawInvariantError, match="anchor or image"):
        Renderer().draw(misplaced)


@pytest.mark.parametrize("figure", [False, True])
def test_renderer_resolves_media_assets(figure: bool) -> None:
    asset = sl.document.Asset("image", "image.png", "image/png", sl.document.InlineAsset(b"image"))
    document = sl.figure(asset) if figure else sl.media(asset, key="image")
    result = sl.planning.plan(document, target=sl.html.target())
    assert result.resources["asset:image"] == asset
    assert 'src="data:image/png;base64,aW1hZ2U="' in Renderer().draw(result.scene, plan=result)


def test_translated_markdown_and_literal_bindings_share_a_structured_html_path() -> None:
    name = "**Ada** <script>"
    value = sl.md_tr(t"Hello **{name}**", key="hello")
    locale = sl.text.Localization("fr", ("hello = **Bonjour** { $name }",))
    result = sl.planning.plan(sl.paragraph(value), target=sl.html.target(), localization=locale)
    html = Renderer().draw(result.scene, plan=result)
    assert "<strong>Bonjour</strong> **Ada** &lt;script&gt;" in html
    assert "<strong>Ada</strong>" not in html


def test_separately_authored_markdown_fragments_stay_separate_in_html() -> None:
    value = sl.md("**") + "Ada" + sl.md("**")
    result = sl.planning.plan(sl.paragraph(value), target=sl.html.target())
    html = Renderer().draw(result.scene, plan=result)
    assert "**Ada**" in html
    assert "<strong>Ada</strong>" not in html


def test_semantic_accents_use_scoped_palette_colours() -> None:
    document = sl.group(
        sl.block("outer", accent=sl.Tone.SUCCESS),
        sl.themed(sl.Palette(success=0x112233), sl.section(sl.heading("inner"), accent=sl.Tone.SUCCESS)),
        sl.article(sl.heading("brand"), accent=sl.AccentRole.BRAND),
    )
    result = sl.planning.plan(document, target=sl.html.target(), palette=sl.Palette(success=0x445566, brand=0x778899))

    rendered = Renderer().draw(result.scene)

    for colour in ("#445566", "#112233", "#778899"):
        assert f"--squid-accent:{colour}" in rendered


def test_custom_role_marker_preserves_status_markup_and_escapes_html() -> None:
    pending = sl.Role("build.pending", sl.Tone.WARNING, marker="<pending> ")
    result = sl.planning.plan(
        sl.status(sl.md("**Review**"), tone=pending),
        target=sl.html.target(),
        palette=sl.Palette(roles={pending.name: 0x123456}),
    )
    rendered = Renderer().draw(result.scene)
    assert "&lt;pending&gt; " in rendered
    assert "<strong>Review</strong>" in rendered
    assert "--squid-accent:#123456" in rendered

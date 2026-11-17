"""Semantic HTML scene data, safety vocabulary, and protocol-one round trips."""

import jsonschema
import pytest

from squid_ui import scene
from squid_ui.content.text import Markup
from squid_ui.ext.html import scene as html_scene
from squid_ui.interactions import ActionMode


def _html_scene() -> scene.Scene[html_scene.HtmlBody]:
    return scene.Scene(
        protocol=scene.Codec.protocol,
        target="html.semantic+squid-ui.html",
        target_version=1,
        body=html_scene.HtmlBody(
            (
                html_scene.HtmlElement(
                    html_scene.HtmlTag.ARTICLE,
                    (
                        html_scene.HtmlElement(html_scene.HtmlTag.H2, (html_scene.HtmlText("Build <one>"),)),
                        html_scene.HtmlElement(
                            html_scene.HtmlTag.P,
                            (html_scene.HtmlText("**Ready**", Markup.DISCORD_MARKDOWN),),
                            attributes=(html_scene.HtmlAttribute(html_scene.HtmlAttributeName.CLASS, "summary"),),
                        ),
                        html_scene.HtmlElement(
                            html_scene.HtmlTag.TIME,
                            (html_scene.HtmlText("27 August 2026"),),
                            time=html_scene.HtmlTimeRef("2026-08-27T12:00:00+00:00", "Europe/Berlin", "F"),
                        ),
                        html_scene.HtmlElement(
                            html_scene.HtmlTag.A,
                            (html_scene.HtmlText("Download"),),
                            url=html_scene.HtmlUrlRef("https://example.invalid/report"),
                            asset=html_scene.HtmlAssetRef("report", "report.txt", "text/plain"),
                        ),
                        html_scene.HtmlElement(
                            html_scene.HtmlTag.BUTTON,
                            (html_scene.HtmlText("Save"),),
                            action=html_scene.HtmlActionRef("build.save", ActionMode.REBASE),
                            route=html_scene.HtmlRouteRef("build:1:save"),
                            form=html_scene.HtmlFormRef("build.edit", "title"),
                        ),
                    ),
                    colour=html_scene.HtmlColourRef(0x5865F2),
                ),
            ),
            locale="en-GB",
        ),
        assets=(scene.Asset(key="report", name="report.txt", media_type="text/plain"),),
    )


def test_html_body_round_trips_through_scene_protocol_one() -> None:
    document = _html_scene()

    restored = scene.Codec.loads(scene.Codec.dumps(document))

    assert restored == document
    assert isinstance(restored.body, html_scene.HtmlBody)
    assert scene.Codec.to_dict(document)["body"]["kind"] == "html"
    jsonschema.validate(scene.Codec.to_dict(document), scene.Codec.schema())


def test_html_scene_vocabulary_rejects_unsafe_tags_and_attributes() -> None:
    assert "style" not in {attribute.value for attribute in html_scene.HtmlAttributeName}
    assert "script" not in {tag.value for tag in html_scene.HtmlTag}
    with pytest.raises(TypeError, match="unsupported HTML scene tag"):
        html_scene.HtmlElement("script")  # type: ignore[arg-type]


def test_html_codec_rejects_tags_and_attributes_outside_the_allowlist() -> None:
    raw = scene.Codec.to_dict(_html_scene())
    body = raw["body"]
    body["children"][0]["tag"] = "script"
    with pytest.raises(ValueError, match="script"):
        scene.Codec.from_dict(raw)

    raw = scene.Codec.to_dict(_html_scene())
    body = raw["body"]
    body["children"][0]["attributes"] = [{"name": "style", "value": "background:url(javascript:x)"}]
    with pytest.raises(ValueError, match="style"):
        scene.Codec.from_dict(raw)

    raw = scene.Codec.to_dict(_html_scene())
    raw["body"]["children"][0]["children"][0]["kind"] = "unknown"
    with pytest.raises(scene.CodecError, match="unknown HTML scene node kind"):
        scene.Codec.from_dict(raw)


def test_html_attribute_requires_finite_number() -> None:
    with pytest.raises(ValueError, match="finite"):
        html_scene.HtmlAttribute(html_scene.HtmlAttributeName.VALUE, float("nan"))

"""Semantic HTML scene data, safety vocabulary, and protocol-one round trips."""

import jsonschema
import pytest

from squid_ui import scene
from squid_ui.interactions import ActionMode
from squid_ui.text import Markup


def _html_scene() -> scene.Scene[scene.HtmlBody]:
    return scene.Scene(
        protocol=scene.Codec.protocol,
        target="html.semantic+squid-ui.html",
        target_version=1,
        body=scene.HtmlBody(
            (
                scene.HtmlElement(
                    scene.HtmlTag.ARTICLE,
                    (
                        scene.HtmlElement(scene.HtmlTag.H2, (scene.HtmlText("Build <one>"),)),
                        scene.HtmlElement(
                            scene.HtmlTag.P,
                            (scene.HtmlText("**Ready**", Markup.DISCORD_MARKDOWN),),
                            attributes=(scene.HtmlAttribute(scene.HtmlAttributeName.CLASS, "summary"),),
                        ),
                        scene.HtmlElement(
                            scene.HtmlTag.TIME,
                            (scene.HtmlText("27 August 2026"),),
                            time=scene.HtmlTimeRef("2026-08-27T12:00:00+00:00", "Europe/Berlin", "F"),
                        ),
                        scene.HtmlElement(
                            scene.HtmlTag.A,
                            (scene.HtmlText("Download"),),
                            url=scene.HtmlUrlRef("https://example.invalid/report"),
                            asset=scene.HtmlAssetRef("report", "report.txt", "text/plain"),
                        ),
                        scene.HtmlElement(
                            scene.HtmlTag.BUTTON,
                            (scene.HtmlText("Save"),),
                            action=scene.HtmlActionRef("build.save", ActionMode.REBASE),
                            route=scene.HtmlRouteRef("build:1:save"),
                            form=scene.HtmlFormRef("build.edit", "title"),
                        ),
                    ),
                    colour=scene.HtmlColourRef(0x5865F2),
                ),
            ),
            locale="en-GB",
        ),
        assets=(scene.Asset("report", "report.txt", "text/plain"),),
    )


def test_html_body_round_trips_through_scene_protocol_one() -> None:
    document = _html_scene()

    restored = scene.Codec.loads(scene.Codec.dumps(document))

    assert restored == document
    assert isinstance(restored.body, scene.HtmlBody)
    assert scene.Codec.to_dict(document)["body"]["kind"] == "html"
    jsonschema.validate(scene.Codec.to_dict(document), scene.Codec.schema())


def test_html_scene_vocabulary_has_no_raw_html_or_style_attribute() -> None:
    assert "style" not in {attribute.value for attribute in scene.HtmlAttributeName}
    assert "script" not in {tag.value for tag in scene.HtmlTag}
    assert not hasattr(scene, "HtmlRaw")

    with pytest.raises(TypeError, match="unsupported HTML scene tag"):
        scene.HtmlElement("script")  # type: ignore[arg-type]


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

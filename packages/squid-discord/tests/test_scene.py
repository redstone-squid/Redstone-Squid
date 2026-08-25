"""Portable document and resolved-scene contracts."""

import json
from datetime import UTC, datetime, timedelta, timezone

import jsonschema
import pytest

from squid_layouts.document import Asset, Document, InlineAsset, as_document
from squid_layouts.emoji import Emoji
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.interactions import ActionPolicy
from squid_layouts.primitives.nodes import Text
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.scene.codec import SceneCodec, SceneCodecError
from squid_layouts.scene.model import (
    SceneAsset,
    SceneButton,
    SceneClassicMessage,
    SceneClassicRow,
    SceneComponentsV2,
    SceneDocument,
    SceneEmbed,
    SceneEmbedAuthor,
    SceneEmbedField,
    SceneEmbedFooter,
    SceneEmbedMedia,
    SceneLink,
    SceneOption,
    ScenePanel,
    ScenePremiumButton,
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
                        SceneText("hello"),
                        SceneTime("2026-08-22T14:30:00+00:00", "R", "Updated: "),
                        SceneZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "Starts: "),
                        SceneRow(
                            (
                                SceneButton("Save", "form.save", ActionStyle.SUCCESS, policy=ActionPolicy.EXCLUSIVE),
                                SceneLink("Docs", "https://example.invalid"),
                            )
                        ),
                        SceneSelect((SceneOption("One", "1"),), "form.choice"),
                    ),
                    accent=0xFF0000,
                ),
            )
        ),
        assets=(SceneAsset("report", "report.txt", "text/plain"),),
    )


def test_document_normalization_keeps_assets() -> None:
    document = Document((Text("hello"),), (Asset("report", "r.txt", "text/plain", InlineAsset(b"x")),))
    assert as_document(document) is document
    assert as_document(Text("hello")).children == (Text("hello"),)


def test_scene_json_is_canonical_and_round_trips() -> None:
    scene = _scene()
    encoded = SceneCodec.dumps(scene)
    assert SceneCodec.loads(encoded) == scene
    assert encoded == SceneCodec.dumps(SceneCodec.loads(encoded))
    assert json.loads(encoded)["body"]["kind"] == "components_v2"
    assert json.loads(encoded)["body"]["children"][0]["kind"] == "panel"


def test_new_component_metadata_round_trips_on_protocol_one() -> None:
    scene = SceneDocument(
        protocol=1,
        target="discord.components-v2",
        target_version=1,
        body=SceneComponentsV2(
            (
                ScenePanel(
                    (
                        SceneRow(
                            (
                                ScenePremiumButton(42),
                                SceneLink(
                                    None,
                                    "https://example.invalid",
                                    Emoji("wave", 7, animated=True),
                                    disabled=True,
                                ),
                            )
                        ),
                        SceneSelect((SceneOption("One", "1", emoji=Emoji("1️⃣")),), "pick"),
                    ),
                    spoiler=True,
                ),
            )
        ),
    )

    assert SceneCodec.loads(SceneCodec.dumps(scene)) == scene
    jsonschema.validate(SceneCodec.to_dict(scene), SceneCodec.schema())


def test_protocol_one_decodes_payloads_without_new_optional_fields() -> None:
    raw = SceneCodec.to_dict(_scene())
    panel = raw["body"]["children"][0]
    panel.pop("spoiler")
    link = panel["children"][3]["items"][1]
    link.pop("emoji")
    link.pop("disabled")
    option = panel["children"][4]["options"][0]
    option.pop("emoji")

    decoded = SceneCodec.from_dict(raw)

    assert decoded.components_v2.children[0].spoiler is False  # type: ignore[union-attr]


def test_scene_fingerprint_is_stable_and_content_sensitive() -> None:
    first = _scene()
    second = SceneDocument(
        SceneCodec.protocol, first.target, first.target_version, SceneComponentsV2((SceneText("different"),))
    )
    assert SceneCodec.fingerprint(first) == SceneCodec.fingerprint(SceneCodec.loads(SceneCodec.dumps(first)))
    assert SceneCodec.fingerprint(first) != SceneCodec.fingerprint(second)


def test_unknown_scene_protocol_fails_explicitly() -> None:
    payload = SceneCodec.to_dict(_scene())
    payload["protocol"] = 99
    with pytest.raises(SceneCodecError, match="unsupported scene protocol"):
        SceneCodec.from_dict(payload)


def test_scene_protocol_exposes_a_deterministic_cross_language_schema() -> None:
    schema = SceneCodec.schema()

    assert schema["properties"]["protocol"] == {"const": SceneCodec.protocol}
    assert "button" in schema["$defs"]
    assert "time" in schema["$defs"]
    assert "zoned_time" in schema["$defs"]
    assert "data" not in SceneCodec.schema_json()
    schema["title"] = "mutated by caller"
    assert SceneCodec.schema()["title"] != "mutated by caller"


def test_timestamp_plans_as_a_typed_utc_scene_instant() -> None:
    import squid_layouts as sl
    from squid_discord import V2_TARGET

    instant = datetime(2026, 8, 22, 16, 30, tzinfo=timezone(timedelta(hours=2)))
    result = sl.planning.plan(
        sl.timestamp(instant, style=sl.semantic.TimeStyle.RELATIVE, label="Updated"), target=V2_TARGET
    )

    assert result.scene.components_v2.children == (SceneTime("2026-08-22T14:30:00+00:00", "R", "**Updated:** "),)


def test_zoned_timestamp_plans_as_an_instant_plus_named_timezone() -> None:
    import squid_layouts as sl
    from squid_discord import V2_TARGET

    value = sl.temporal.ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "America/New_York")

    result = sl.planning.plan(sl.zoned_timestamp(value, label="Starts"), target=V2_TARGET)

    assert result.scene.components_v2.children == (
        SceneZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "**Starts:** "),
    )


def _classic_scene() -> SceneDocument:
    return SceneDocument(
        protocol=SceneCodec.protocol,
        target="discord.components-v1",
        target_version=1,
        body=SceneClassicMessage(
            content="@here the build is ready",
            embeds=(
                SceneEmbed(
                    title="Piston door",
                    url="https://example.invalid/door",
                    description="A 2x2 flush door.",
                    fields=(SceneEmbedField("Width", "2", inline=True), SceneEmbedField("Notes", "seamless")),
                    footer=SceneEmbedFooter("Submitted by squid", "https://example.invalid/icon.png"),
                    author=SceneEmbedAuthor("Redstone Squid", "https://example.invalid", None),
                    colour=0x00FF00,
                    image=SceneEmbedMedia("https://example.invalid/i.png", "the door"),
                    thumbnail=SceneEmbedMedia("https://example.invalid/t.png"),
                    timestamp="2026-08-22T14:30:00+00:00",
                ),
            ),
            rows=(
                SceneClassicRow(
                    (
                        SceneButton("Save", "form.save", ActionStyle.SUCCESS, policy=ActionPolicy.EXCLUSIVE),
                        SceneLink("Docs", "https://example.invalid"),
                    )
                ),
                SceneClassicRow((SceneSelect((SceneOption("One", "1"),), "form.choice"),)),
            ),
        ),
        assets=(SceneAsset("report", "report.txt", "text/plain"),),
    )


class TestClassicBody:
    def test_a_classic_scene_round_trips_canonically(self) -> None:
        scene = _classic_scene()
        encoded = SceneCodec.dumps(scene)

        assert SceneCodec.loads(encoded) == scene
        assert encoded == SceneCodec.dumps(SceneCodec.loads(encoded))
        assert json.loads(encoded)["body"]["kind"] == "classic_message"

    def test_both_bodies_validate_against_the_published_schema(self) -> None:
        jsonschema.validate(SceneCodec.to_dict(_scene()), SceneCodec.schema())
        jsonschema.validate(SceneCodec.to_dict(_classic_scene()), SceneCodec.schema())

    def test_an_unknown_body_kind_is_refused_by_name(self) -> None:
        raw = SceneCodec.to_dict(_scene())
        raw["body"] = {"kind": "carrier pigeon"}

        with pytest.raises(SceneCodecError, match="unknown scene body kind 'carrier pigeon'"):
            SceneCodec.from_dict(raw)

    def test_a_classic_body_has_no_components_v2_children_to_offer(self) -> None:
        with pytest.raises(LayoutInvariantError, match="SceneClassicMessage body, not Components V2"):
            _ = _classic_scene().components_v2

    def test_two_bodies_of_different_kinds_never_share_a_fingerprint(self) -> None:
        assert SceneCodec.fingerprint(_scene()) != SceneCodec.fingerprint(_classic_scene())

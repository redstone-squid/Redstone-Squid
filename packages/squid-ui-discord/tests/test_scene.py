"""Portable document and resolved-scene contracts."""

import json
from datetime import UTC, datetime, timedelta, timezone

import jsonschema
import pytest

from squid_ui import scene
from squid_ui.document import Asset, Document, InlineAsset, as_document
from squid_ui.emoji import Emoji
from squid_ui.errors import LayoutInvariantError
from squid_ui.interactions import ActionMode
from squid_ui.primitives.nodes import Text
from squid_ui.primitives.styles import ActionStyle


def _scene() -> scene.Document:
    return scene.Document(
        protocol=scene.Codec.protocol,
        target="discord.components-v2",
        target_version=1,
        body=scene.ComponentsV2(
            (
                scene.Panel(
                    (
                        scene.Text("hello"),
                        scene.Time("2026-08-22T14:30:00+00:00", "R", "Updated: "),
                        scene.ZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "Starts: "),
                        scene.Row(
                            (
                                scene.Button("Save", "form.save", ActionStyle.SUCCESS, mode=ActionMode.EXCLUSIVE),
                                scene.Link("Docs", "https://example.invalid"),
                            )
                        ),
                        scene.Select((scene.Option("One", "1"),), "form.choice"),
                    ),
                    accent=0xFF0000,
                ),
            )
        ),
        assets=(scene.Asset("report", "report.txt", "text/plain"),),
    )


def test_document_normalization_keeps_assets() -> None:
    document = Document((Text("hello"),), (Asset("report", "r.txt", "text/plain", InlineAsset(b"x")),))
    assert as_document(document) is document
    assert as_document(Text("hello")).children == (Text("hello"),)


def test_scene_json_is_canonical_and_round_trips() -> None:
    document = _scene()
    encoded = scene.Codec.dumps(document)
    assert scene.Codec.loads(encoded) == document
    assert encoded == scene.Codec.dumps(scene.Codec.loads(encoded))
    assert json.loads(encoded)["body"]["kind"] == "components_v2"
    assert json.loads(encoded)["body"]["children"][0]["kind"] == "panel"


def test_scene_wire_uses_the_python_markup_and_mode_vocabulary() -> None:
    encoded = scene.Codec.dumps(_scene())

    assert '"markup":"discord-markdown"' in encoded
    assert '"mode":"exclusive"' in encoded
    assert '"dialect"' not in encoded
    assert '"policy"' not in encoded


def test_new_component_metadata_round_trips_on_protocol_one() -> None:
    document = scene.Document(
        protocol=1,
        target="discord.components-v2",
        target_version=1,
        body=scene.ComponentsV2(
            (
                scene.Panel(
                    (
                        scene.Row(
                            (
                                scene.PremiumButton(42),
                                scene.Link(
                                    None,
                                    "https://example.invalid",
                                    Emoji("wave", 7, animated=True),
                                    disabled=True,
                                ),
                            )
                        ),
                        scene.Select((scene.Option("One", "1", emoji=Emoji("1️⃣")),), "pick"),
                    ),
                    spoiler=True,
                ),
            )
        ),
    )

    assert scene.Codec.loads(scene.Codec.dumps(document)) == document
    jsonschema.validate(scene.Codec.to_dict(document), scene.Codec.schema())


def test_protocol_one_decodes_payloads_without_new_optional_fields() -> None:
    raw = scene.Codec.to_dict(_scene())
    panel = raw["body"]["children"][0]
    panel.pop("spoiler")
    link = panel["children"][3]["items"][1]
    link.pop("emoji")
    link.pop("disabled")
    option = panel["children"][4]["options"][0]
    option.pop("emoji")

    decoded = scene.Codec.from_dict(raw)

    assert decoded.components_v2.children[0].spoiler is False  # type: ignore[union-attr]


def test_scene_fingerprint_is_stable_and_content_sensitive() -> None:
    first = _scene()
    second = scene.Document(
        scene.Codec.protocol, first.target, first.target_version, scene.ComponentsV2((scene.Text("different"),))
    )
    assert scene.Codec.fingerprint(first) == scene.Codec.fingerprint(scene.Codec.loads(scene.Codec.dumps(first)))
    assert scene.Codec.fingerprint(first) != scene.Codec.fingerprint(second)


def test_unknown_scene_protocol_fails_explicitly() -> None:
    payload = scene.Codec.to_dict(_scene())
    payload["protocol"] = 99
    with pytest.raises(scene.CodecError, match="unsupported scene protocol"):
        scene.Codec.from_dict(payload)


def test_scene_protocol_exposes_a_deterministic_cross_language_schema() -> None:
    schema = scene.Codec.schema()

    assert schema["properties"]["protocol"] == {"const": scene.Codec.protocol}
    assert "button" in schema["$defs"]
    assert "time" in schema["$defs"]
    assert "zoned_time" in schema["$defs"]
    assert "data" not in scene.Codec.schema_json()
    schema["title"] = "mutated by caller"
    assert scene.Codec.schema()["title"] != "mutated by caller"


def test_timestamp_plans_as_a_typed_utc_scene_instant() -> None:
    import squid_ui as sl
    from squid_ui_discord import DISCORD_V2_DPY27

    instant = datetime(2026, 8, 22, 16, 30, tzinfo=timezone(timedelta(hours=2)))
    result = sl.planning.plan(
        sl.timestamp(instant, style=sl.semantic.TimeStyle.RELATIVE, label="Updated"), target=DISCORD_V2_DPY27
    )

    assert result.scene.components_v2.children == (scene.Time("2026-08-22T14:30:00+00:00", "R", "**Updated:** "),)


def test_zoned_timestamp_plans_as_an_instant_plus_named_timezone() -> None:
    import squid_ui as sl
    from squid_ui_discord import DISCORD_V2_DPY27

    value = sl.temporal.ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "America/New_York")

    result = sl.planning.plan(sl.zoned_timestamp(value, label="Starts"), target=DISCORD_V2_DPY27)

    assert result.scene.components_v2.children == (
        scene.ZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "**Starts:** "),
    )


def _classic_scene() -> scene.Document:
    return scene.Document(
        protocol=scene.Codec.protocol,
        target="discord.components-v1",
        target_version=1,
        body=scene.ClassicMessage(
            content="@here the build is ready",
            embeds=(
                scene.Embed(
                    title="Piston door",
                    url="https://example.invalid/door",
                    description="A 2x2 flush door.",
                    fields=(scene.EmbedField("Width", "2", inline=True), scene.EmbedField("Notes", "seamless")),
                    footer=scene.EmbedFooter("Submitted by squid", "https://example.invalid/icon.png"),
                    author=scene.EmbedAuthor("Redstone Squid", "https://example.invalid", None),
                    colour=0x00FF00,
                    image=scene.EmbedMedia("https://example.invalid/i.png", "the door"),
                    thumbnail=scene.EmbedMedia("https://example.invalid/t.png"),
                    timestamp="2026-08-22T14:30:00+00:00",
                ),
            ),
            rows=(
                scene.ClassicRow(
                    (
                        scene.Button("Save", "form.save", ActionStyle.SUCCESS, mode=ActionMode.EXCLUSIVE),
                        scene.Link("Docs", "https://example.invalid"),
                    )
                ),
                scene.ClassicRow((scene.Select((scene.Option("One", "1"),), "form.choice"),)),
            ),
        ),
        assets=(scene.Asset("report", "report.txt", "text/plain"),),
    )


class TestClassicBody:
    def test_a_classic_scene_round_trips_canonically(self) -> None:
        document = _classic_scene()
        encoded = scene.Codec.dumps(document)

        assert scene.Codec.loads(encoded) == document
        assert encoded == scene.Codec.dumps(scene.Codec.loads(encoded))
        assert json.loads(encoded)["body"]["kind"] == "classic_message"

    def test_both_bodies_validate_against_the_published_schema(self) -> None:
        jsonschema.validate(scene.Codec.to_dict(_scene()), scene.Codec.schema())
        jsonschema.validate(scene.Codec.to_dict(_classic_scene()), scene.Codec.schema())

    def test_an_unknown_body_kind_is_refused_by_name(self) -> None:
        raw = scene.Codec.to_dict(_scene())
        raw["body"] = {"kind": "carrier pigeon"}

        with pytest.raises(scene.CodecError, match="unknown scene body kind 'carrier pigeon'"):
            scene.Codec.from_dict(raw)

    def test_a_classic_body_has_no_components_v2_children_to_offer(self) -> None:
        with pytest.raises(LayoutInvariantError, match="ClassicMessage body, not Components V2"):
            _ = _classic_scene().components_v2

    def test_two_bodies_of_different_kinds_never_share_a_fingerprint(self) -> None:
        assert scene.Codec.fingerprint(_scene()) != scene.Codec.fingerprint(_classic_scene())

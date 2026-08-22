"""Portable document and resolved-scene contracts."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from squid_layouts.actions import ActionPolicy
from squid_layouts.document import Asset, Document, InlineAsset, as_document
from squid_layouts.primitives.nodes import Text
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.scene.codec import SceneCodec, SceneCodecError
from squid_layouts.scene.model import (
    SceneAsset,
    SceneButton,
    SceneDocument,
    SceneLink,
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
        children=(
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
    assert json.loads(encoded)["children"][0]["kind"] == "panel"


def test_scene_fingerprint_is_stable_and_content_sensitive() -> None:
    first = _scene()
    second = SceneDocument(SceneCodec.protocol, first.target, first.target_version, (SceneText("different"),))
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
    from squid_layouts.discord import DEFAULT_TARGET

    instant = datetime(2026, 8, 22, 16, 30, tzinfo=timezone(timedelta(hours=2)))
    result = sl.plan(sl.timestamp(instant, style=sl.TimeStyle.RELATIVE, label="Updated"), target=DEFAULT_TARGET)

    assert result.scene.children == (SceneTime("2026-08-22T14:30:00+00:00", "R", "**Updated:** "),)


def test_zoned_timestamp_plans_as_an_instant_plus_named_timezone() -> None:
    import squid_layouts as sl
    from squid_layouts.discord import DEFAULT_TARGET

    value = sl.ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "America/New_York")

    result = sl.plan(sl.zoned_timestamp(value, label="Starts"), target=DEFAULT_TARGET)

    assert result.scene.children == (SceneZonedTime("2026-08-22T14:30:00+00:00", "America/New_York", "**Starts:** "),)

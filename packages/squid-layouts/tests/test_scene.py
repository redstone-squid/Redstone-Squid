"""Portable document and resolved-scene contracts."""

import json

import pytest

from squid_layouts.actions import ActionPolicy
from squid_layouts.document import Asset, Document, InlineAsset, as_document
from squid_layouts.ir import Text
from squid_layouts.scene import (
    SceneAsset,
    SceneButton,
    SceneDocument,
    SceneLink,
    SceneOption,
    ScenePanel,
    SceneRow,
    SceneSelect,
    SceneText,
)
from squid_layouts.scene_codec import SceneCodec, SceneCodecError
from squid_layouts.styles import ActionStyle


def _scene() -> SceneDocument:
    return SceneDocument(
        protocol=0,
        target="discord.components-v2",
        target_version=1,
        children=(
            ScenePanel(
                (
                    SceneText("hello"),
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
    second = SceneDocument(0, first.target, first.target_version, (SceneText("different"),))
    assert SceneCodec.fingerprint(first) == SceneCodec.fingerprint(SceneCodec.loads(SceneCodec.dumps(first)))
    assert SceneCodec.fingerprint(first) != SceneCodec.fingerprint(second)


def test_unknown_scene_protocol_fails_explicitly() -> None:
    payload = SceneCodec.to_dict(_scene())
    payload["protocol"] = 99
    with pytest.raises(SceneCodecError, match="unsupported scene protocol"):
        SceneCodec.from_dict(payload)

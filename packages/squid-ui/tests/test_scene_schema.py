import json
from pathlib import Path

from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.html import scene as html_scene
from squid_ui.ext.slack import scene as slack_scene
from squid_ui.scene import Codec

# Importing these vocabularies registers the schemas included in this artifact.
_SCENE_VOCABULARIES = (discord_scene, html_scene, slack_scene)

SCHEMA_ARTIFACT = Path(__file__).parents[3] / "docs" / "public" / "schema" / "scene-v1.schema.json"


def test_documentation_schema_matches_codec() -> None:
    assert json.loads(SCHEMA_ARTIFACT.read_text(encoding="utf-8")) == Codec.schema()


def test_schema_requires_emitted_defaults_and_closes_helper_objects() -> None:
    defs = Codec.schema()["$defs"]
    assert defs["classic_row"]["properties"]["controls"]["maxItems"] == 5
    for name in ("classic_row", "slack_text", "slack_option"):
        assert defs[name]["additionalProperties"] is False
    for name, field in (
        ("text", "markup"),
        ("button", "disabled"),
        ("slack_text", "kind"),
        ("slack_select", "select_kind"),
    ):
        assert field in defs[name]["required"]


def test_documentation_schema_has_public_canonical_id() -> None:
    schema = Codec.schema()

    assert schema["$id"] == "https://redstone-squid.github.io/Redstone-Squid/schema/scene-v1.schema.json"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

import json
from pathlib import Path

from squid_ui.scene import Codec

SCHEMA_ARTIFACT = Path(__file__).parents[3] / "docs" / "schema" / "scene-v1.schema.json"


def test_documentation_schema_matches_codec() -> None:
    assert json.loads(SCHEMA_ARTIFACT.read_text(encoding="utf-8")) == Codec.schema()


def test_documentation_schema_has_public_canonical_id() -> None:
    schema = Codec.schema()

    assert schema["$id"] == "https://redstone-squid.github.io/Redstone-Squid/schema/scene-v1.schema.json"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

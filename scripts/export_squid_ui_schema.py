"""Export the public squid-ui scene schema for the documentation site."""

from pathlib import Path

from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.html import scene as html_scene
from squid_ui.ext.slack import scene as slack_scene
from squid_ui.scene import Codec

# Importing these vocabularies registers the schemas included in this artifact.
_SCENE_VOCABULARIES = (discord_scene, html_scene, slack_scene)


def main() -> None:
    """Write the canonical, human-readable scene protocol schema."""
    destination = Path(__file__).parents[1] / "docs" / "schema" / "scene-v1.schema.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(Codec.schema_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

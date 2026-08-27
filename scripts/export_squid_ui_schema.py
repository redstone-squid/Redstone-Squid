"""Export the public squid-ui scene schema for the documentation site."""

from pathlib import Path

from squid_ui.scene import Codec


def main() -> None:
    """Write the canonical, human-readable scene protocol schema."""
    destination = Path(__file__).parents[1] / "docs" / "schema" / "scene-v1.schema.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(Codec.schema_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

"""Export the API's deterministic OpenAPI document for generated clients."""

import json
from pathlib import Path

from squid.api.app import create_api_app

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"


def main() -> None:
    """Write the current application contract to the canonical contract workspace."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = create_api_app().openapi()
    OUTPUT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

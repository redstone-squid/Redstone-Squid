"""Export versioned finding-envelope schemas for untrusted workflow artifacts."""

import json

from squid import PROJECT_ROOT
from tests.fuzz.artifacts import FindingCandidateV1, QualifiedFindingV1

OUTPUT_DIRECTORY = PROJECT_ROOT / "contracts" / "fuzz"
MODELS = {
    "finding-candidate-v1.schema.json": FindingCandidateV1,
    "qualified-finding-v1.schema.json": QualifiedFindingV1,
}


def main() -> None:
    """Write deterministic JSON Schemas for every cross-workflow finding envelope."""
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, model in MODELS.items():
        document = model.model_json_schema(mode="validation")
        output = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (OUTPUT_DIRECTORY / filename).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()

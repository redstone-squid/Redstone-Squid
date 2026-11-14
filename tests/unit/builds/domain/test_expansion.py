import pytest
from pydantic import TypeAdapter, ValidationError

from squid.builds.application.edit_schema import BuildPatch
from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import BuildCategory, BuildDraft
from squid.builds.domain.expansion import DoorExpansion, ExpansionParameter
from squid.builds.errors import InvalidBuildError
from squid.builds.models import DoorBuild, OtherBuild
from squid.submissions.infrastructure.inference_payloads import BuildDraftPayload
from tests.support.expansion import expansion_document


def test_document_preserves_source_algebra_and_partial_bounds() -> None:
    document = expansion_document()
    restored = DoorExpansion.model_validate_json(document.model_dump_json())
    assert restored == document
    assert restored.parameters[0].maximum_kind == "unbounded"
    assert restored.parameters[1].maximum == 14
    assert restored.parameters[2].maximum_kind == "unknown"
    assert restored.branches[1].volume == "ceil(3x/2) + 8"


@pytest.mark.parametrize(
    "values",
    [
        {"maximum_kind": "finite"},
        {"maximum": 8},
        {"minimum": 9, "maximum_kind": "finite", "maximum": 8},
        {"repeating_steps": []},
        {"repeating_steps": [0]},
        {"repeating_steps": [True]},
    ],
)
def test_invalid_structured_domain_is_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExpansionParameter.model_validate({"name": "n", **values})


def test_inference_payload_and_finalized_build_keep_expansion() -> None:
    document = expansion_document()
    draft = BuildDraft(category=BuildCategory.DOOR, door_expansion=document)
    adapter = TypeAdapter(BuildDraftPayload)
    payload = adapter.validate_json(adapter.dump_json(BuildDraftPayload.from_draft(draft)))
    build = payload.to_draft().finalize()
    assert isinstance(build, DoorBuild)
    assert build.expansion == document
    assert build.door_dimensions == (None, None, None)
    assert adapter.validate_python({}).door_expansion is None


def test_expansion_patch_supports_omission_replacement_and_clear() -> None:
    document = expansion_document()
    build = DoorBuild(expansion=document)
    BuildEditPatch().apply(build)
    assert build.expansion == document
    patch = BuildPatch.model_validate({"door": {"expansion": document.model_dump(mode="json")}})
    typed = BuildEditPatch.from_attributes(patch.edit_attributes())
    BuildEditPatch.combine([typed]).apply(build)
    assert build.expansion == document
    BuildEditPatch.from_attributes(BuildPatch.model_validate({"door": {"expansion": None}}).edit_attributes()).apply(
        build
    )
    assert build.expansion is None
    with pytest.raises(InvalidBuildError):
        typed.apply(OtherBuild())

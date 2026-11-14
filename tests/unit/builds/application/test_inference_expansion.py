"""Model expansion mistakes stay local to partial, source-grounded facts."""

import pytest

from squid.builds.application.inference_expansion import InferredDoorExpansion, InferredExpansionParameter
from squid.builds.domain.expansion import ExpansionBranch, ExpansionEvidence

SOURCES = {10: "3-high expandable door. 40+20x blocks, x=1-14 wide; breaks record at: n=5-14 wide"}


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(None, None), (14, 1)],
)
def test_inconsistent_bounds_remain_unknown_while_the_original_domain_survives(
    minimum: int | None,
    maximum: int | None,
) -> None:
    value = InferredDoorExpansion(
        parameters=[InferredExpansionParameter(name="x", minimum=minimum, maximum_kind="finite", maximum=maximum)],
        branches=[ExpansionBranch(opening_width="x", opening_height="3", domain="x=1-14 wide", volume="40+20x blocks")],
    )
    document = value.to_domain(SOURCES)
    [parameter] = document.parameters
    assert parameter.minimum is None
    assert parameter.maximum is None
    assert parameter.maximum_kind == "unknown"
    assert document.branches[0].domain == "x=1-14 wide"
    assert document.branches[0].volume == "40+20x blocks"


def test_valid_bounds_and_duplicate_parameter_names_do_not_fail_the_document() -> None:
    parameter = InferredExpansionParameter(name="x", minimum=1, maximum=14)
    document = InferredDoorExpansion(parameters=[parameter, parameter]).to_domain(SOURCES)
    assert len(document.parameters) == 1
    assert document.parameters[0].maximum_kind == "finite"
    assert document.parameters[0].maximum == 14


def test_generated_summaries_and_foreign_source_ids_are_replaced_with_real_excerpts() -> None:
    value = InferredDoorExpansion(
        raw_description="The author says this expandable door is smaller and breaks the record for several widths.",
        evidence=[ExpansionEvidence(source_message_id=99, text="40+20x blocks")],
        branches=[
            ExpansionBranch(
                domain="x=1-14 wide",
                volume="40+20x blocks",
                claimed_record_domain="n=5-14 wide",
                evidence=[ExpansionEvidence(source_message_id=10, text="A summary that never appeared in the source")],
            )
        ],
    )
    document = value.to_domain(SOURCES)
    assert document.raw_description is not None
    assert "author" not in document.raw_description
    assert all(line in SOURCES[10] for line in document.raw_description.splitlines())
    assert document.evidence
    for evidence in (*document.evidence, *document.branches[0].evidence):
        assert evidence.source_message_id == 10
        assert evidence.text in SOURCES[10]


def test_invented_algebra_is_not_accepted_as_a_source_expression() -> None:
    value = InferredDoorExpansion(
        raw_description="40+20x blocks",
        evidence=[ExpansionEvidence(source_message_id=10, text="40+20x blocks")],
        branches=[ExpansionBranch(volume="20*(x+2)", opening_width="invented_parameter")],
        parameters=[InferredExpansionParameter(name="invented_parameter")],
    )
    document = value.to_domain(SOURCES)
    assert document.parameters == ()
    assert document.branches[0].volume is None
    assert document.branches[0].opening_width is None
    assert document.raw_description == "40+20x blocks"


def test_raw_source_fallback_does_not_repeat_excerpts_covered_by_a_longer_quote() -> None:
    excerpt = "40+20x blocks, x=1-14 wide"
    value = InferredDoorExpansion(
        raw_description="The volume grows with width.",
        evidence=[ExpansionEvidence(source_message_id=10, text=excerpt)],
        branches=[ExpansionBranch(volume="40+20x blocks", domain="x=1-14 wide")],
    )
    assert value.to_domain(SOURCES).raw_description == excerpt


def test_a_variable_must_not_be_an_accidental_letter_in_an_unrelated_word() -> None:
    value = InferredDoorExpansion(
        parameters=[InferredExpansionParameter(name="n")],
        branches=[ExpansionBranch(opening_width="n")],
    )
    document = value.to_domain({10: "contained expandable door"})
    assert document.parameters == ()
    assert document.branches[0].opening_width is None

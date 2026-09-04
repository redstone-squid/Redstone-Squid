"""Attachment identity, primary selection, and duplicate evidence semantics."""

import pytest

from squid.bot.submission.attachment_enrichment import (
    AttachmentFailure,
    AttachmentLifecycle,
    default_only_usable,
    merge_duplicate_evidence,
    primary_schematic,
    select_primary,
)
from squid.bot.submission.attachments import ClassifiedAttachment
from squid.schematics.application import DuplicateCandidate, IngestedSchematic, IngestRequest
from tests.unit.schematics.fakes import make_analysis


def schematic(identity: str, filename: str, *, failed: bool = False) -> AttachmentLifecycle:
    classified = ClassifiedAttachment("schematic", filename, "application/octet-stream")
    request = IngestRequest(data=identity.encode(), filename=filename)
    return AttachmentLifecycle(
        identity,
        filename,
        classification=classified,
        request=request,
        analysis=None if failed else IngestedSchematic(identity * 64, make_analysis()),
        failure=AttachmentFailure("analysis", "The file could not be analyzed.") if failed else None,
    )


def test_zero_or_all_failed_schematics_need_no_primary() -> None:
    attachments = default_only_usable((schematic("a", "broken.litematic", failed=True),))

    assert primary_schematic(()) is None
    assert primary_schematic(attachments) is None


def test_the_only_usable_schematic_defaults_even_when_it_is_not_first() -> None:
    failed = schematic("a", "broken.litematic", failed=True)
    usable = schematic("b", "working.litematic")

    attachments = default_only_usable((failed, usable))

    assert primary_schematic(attachments) == attachments[1]


def test_many_schematics_require_identity_selection_and_survive_reordering() -> None:
    first = schematic("a", "first.litematic")
    second = schematic("b", "second.litematic")

    assert primary_schematic(default_only_usable((first, second))) is None

    selected = select_primary((second, first), "a")
    reordered = tuple(reversed(selected))

    primary = primary_schematic(reordered)
    assert primary is not None
    assert primary.identity == "a"


def test_a_failed_schematic_cannot_be_selected() -> None:
    failed = schematic("a", "broken.litematic", failed=True)

    with pytest.raises(ValueError, match="not a usable schematic"):
        select_primary((failed,), "a")


def test_duplicate_evidence_keeps_strongest_match_and_all_source_identities() -> None:
    first = schematic("a", "first.litematic")
    second = schematic("b", "second.litematic")
    matches = (
        (first, DuplicateCandidate(7, 70, "near", 0.5)),
        (second, DuplicateCandidate(7, 71, "identical", 0.0)),
        (first, DuplicateCandidate(8, 80, "structural-match", 0.0)),
    )

    evidence = merge_duplicate_evidence(reversed(matches), {7: "2x2 Seamless Door", 8: "3x3 Door"})

    assert evidence == [
        {
            "build_id": 7,
            "title": "2x2 Seamless Door",
            "tier": "identical",
            "footprint_distance": 0.0,
            "source_attachments": [
                {"attachment_id": "a", "filename": "first.litematic"},
                {"attachment_id": "b", "filename": "second.litematic"},
            ],
        },
        {
            "build_id": 8,
            "title": "3x3 Door",
            "tier": "structural-match",
            "footprint_distance": 0.0,
            "source_attachments": [{"attachment_id": "a", "filename": "first.litematic"}],
        },
    ]

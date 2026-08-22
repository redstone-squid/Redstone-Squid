"""Semantic content is lossless unless the author explicitly grants loss."""

import pytest

from squid_layouts import (
    Paragraph,
    UnsolvableLayoutError,
    best_effort,
    optional,
    plan,
    truncate,
)
from squid_layouts.discord import DEFAULT_TARGET
from squid_layouts.planning import SolveNoteCode


def test_semantic_prose_is_lossless_by_default() -> None:
    with pytest.raises(UnsolvableLayoutError, match="Never nodes need"):
        plan(Paragraph("x" * 5000), target=DEFAULT_TARGET)


def test_truncate_explicitly_grants_prose_loss() -> None:
    result = plan(truncate(Paragraph("x" * 5000)), target=DEFAULT_TARGET)

    assert len(result.scene.components_v2.children[0].content) == 4000  # type: ignore[union-attr]
    assert result.report.events[0].severity.value == "degradation"


def test_optional_explicitly_grants_whole_node_loss() -> None:
    result = plan((Paragraph("x" * 4000), optional(Paragraph("footnote"))), target=DEFAULT_TARGET)

    assert len(result.scene.components_v2.children) == 1
    assert any(event.code == f"layout.{SolveNoteCode.NODE_DROPPED}" for event in result.report.events)


def test_best_effort_only_relaxes_prose() -> None:
    result = plan(best_effort(Paragraph("x" * 5000)), target=DEFAULT_TARGET)

    assert len(result.scene.components_v2.children[0].content) == 4000  # type: ignore[union-attr]

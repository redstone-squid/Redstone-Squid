"""Semantic content is lossless unless the author explicitly grants loss."""

import pytest

from squid_discord import DISCORD_V2_DPY27
from squid_layouts import best_effort, optional, truncate
from squid_layouts.errors import UnsolvableLayoutError
from squid_layouts.planning import SolveNoteCode, plan
from squid_layouts.semantic import Importance, Paragraph


def test_semantic_prose_is_lossless_by_default() -> None:
    with pytest.raises(UnsolvableLayoutError, match="Never nodes need"):
        plan(Paragraph("x" * 5000), target=DISCORD_V2_DPY27)


def test_truncate_explicitly_grants_prose_loss() -> None:
    result = plan(truncate(Paragraph("x" * 5000)), target=DISCORD_V2_DPY27)

    assert len(result.scene.components_v2.children[0].content) == 4000  # type: ignore[union-attr]
    assert result.report.events[0].severity.value == "degradation"


def test_optional_explicitly_grants_whole_node_loss() -> None:
    result = plan((Paragraph("x" * 4000), optional(Paragraph("footnote"))), target=DISCORD_V2_DPY27)

    assert len(result.scene.components_v2.children) == 1
    assert any(event.code == f"layout.{SolveNoteCode.OPTIONAL_DROPPED}" for event in result.report.events)


def test_optional_importance_orders_whole_region_loss() -> None:
    result = plan(
        (
            Paragraph("x" * 3980),
            optional(Paragraph("low priority"), importance=Importance.LOW),
            optional(Paragraph("high priority"), importance=Importance.HIGH),
        ),
        target=DISCORD_V2_DPY27,
    )

    contents = [child.content for child in result.scene.components_v2.children]  # type: ignore[union-attr]
    assert contents == ["x" * 3980, "high priority"]


def test_best_effort_only_relaxes_prose() -> None:
    result = plan(best_effort(Paragraph("x" * 5000)), target=DISCORD_V2_DPY27)

    assert len(result.scene.components_v2.children[0].content) == 4000  # type: ignore[union-attr]

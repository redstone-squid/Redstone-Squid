import pytest

import squid_ui as sl
from squid_ui.errors import LayoutSearchExhaustedError
from squid_ui.planning.allocation import CertifiedPage, PageFamily
from squid_ui.planning.allocation_frontier import AllocationFrontier
from squid_ui.planning.certification import FamilyCertificate
from squid_ui.planning.composition import (
    CandidateComposition,
    OccurrenceId,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    WorkBudget,
)
from squid_ui.planning.semantic_index import semantic_graph
from squid_ui.target_types import RenderTarget


def test_interrupted_replacement_preserves_the_previous_certificate() -> None:
    graph = semantic_graph(sl.paragraph("Body"))
    root = next(reference for reference, unit in graph.units.items() if unit.parent is None)
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), graph, root)
    offer = RealizationOffer[RenderTarget, object, object](occurrence, "body", object())
    family = PageFamily(CandidateComposition(offer), {})
    certificate = FamilyCertificate({}, {}, (CertifiedPage((), "body"),))
    work = WorkBudget(1000)
    complete = AllocationFrontier[RenderTarget, object, object, str](PlanningContext(work), {})
    complete.consider(family, ((offer, 1),), {}, certificate)
    complete.consider(family, ((offer, 0),), {}, certificate)
    assert complete.best()[1][0].preferences.ranks == (0,)

    # The second quality dominates the first, but its final work charge can
    # exhaust the allowance before the replacement snapshot is retained.
    bounded = AllocationFrontier[RenderTarget, object, object, str](PlanningContext(WorkBudget(work.spent - 1)), {})
    bounded.consider(family, ((offer, 1),), {}, certificate)
    previous = bounded.best()[0]
    with pytest.raises(LayoutSearchExhaustedError):
        bounded.consider(family, ((offer, 0),), {}, certificate)
    assert bounded.best()[0] is previous

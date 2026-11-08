"""Candidate recovery screens keep every retained inference result reachable."""

from uuid import uuid4

from squid.bot.submission.ui.inference import InferenceCandidatesScreen
from squid.builds.domain import BuildDraft
from squid.submissions.application.inference_runs import InferenceCandidate
from tests.support.services import bot_services


def test_candidate_pages_cover_results_beyond_discords_choice_limit() -> None:
    run_id = uuid4()
    candidates = tuple(InferenceCandidate(uuid4(), run_id, 7, BuildDraft()) for _ in range(31))
    screen = InferenceCandidatesScreen(bot_services(), run_id, candidates)

    assert screen.page_count == 2
    assert screen.visible_candidates == candidates[:25]
    screen.page = 1
    assert screen.visible_candidates == candidates[25:]

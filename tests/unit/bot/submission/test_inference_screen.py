"""Candidate windows keep every result reachable through runtime pagination."""

from uuid import uuid4

from squid.bot.submission.ui.inference import InferenceCandidatesScreen
from squid.builds.domain import BuildDraft
from squid.submissions.application.inference_runs import InferenceCandidate
from squid_ui.ext.discord.testing import stage
from tests.support.services import bot_services


async def test_candidate_pages_cover_results_beyond_discords_choice_limit() -> None:
    run_id = uuid4()
    candidates = tuple(InferenceCandidate(uuid4(), run_id, 7, BuildDraft()) for _ in range(31))
    screen = InferenceCandidatesScreen(bot_services(), run_id, candidates)
    async with stage(screen, owner=object()) as ui:
        first = ui.control(ui.key_for("candidate-0")).payload["options"]
        assert [option["value"] for option in first] == [str(item.id) for item in candidates[:25]]
        await ui.select(ui.key_for("candidate-0"), [str(candidates[0].id)])
        ui.control(ui.key_for("open"))
        await ui.press(ui.key_for("__cursor_next.$/candidates"))
        last = ui.control(ui.key_for("candidate-25")).payload["options"]
        assert [option["value"] for option in last] == [str(item.id) for item in candidates[25:]]
        assert not any(key.endswith(".open") for key in ui.root.snapshot().handler_keys)
        await ui.select(ui.key_for("candidate-25"), [str(candidates[-1].id)])
        ui.control(ui.key_for("open"))
        ui.assert_within_limits()

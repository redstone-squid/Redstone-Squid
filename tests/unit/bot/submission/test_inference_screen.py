"""Candidate menus show results beyond the option limit of a single menu."""

from uuid import uuid4

from squid.bot.submission.ui.inference import InferenceCandidatesScreen
from squid.core.builds.domain import BuildInput
from squid.core.submissions.application.inference_runs import InferenceCandidate
from squid_ui.ext.discord.testing import stage
from tests.support.services import bot_services


async def test_candidate_menus_share_a_page_when_they_fit() -> None:
    run_id = uuid4()
    candidates = tuple(InferenceCandidate(uuid4(), run_id, 7, BuildInput()) for _ in range(31))
    screen = InferenceCandidatesScreen(bot_services(), run_id, candidates)
    async with stage(screen, owner=object()) as ui:
        first = ui.control("candidate-0").payload["options"]
        assert [option["value"] for option in first] == [str(item.id) for item in candidates[:25]]
        await ui.select("candidate-0", [str(candidates[0].id)])
        ui.control("open")
        last = ui.control("candidate-25").payload["options"]
        assert [option["value"] for option in last] == [str(item.id) for item in candidates[25:]]
        assert not any("__cursor_next." in key for key in ui.root.snapshot().handler_keys)
        await ui.select("candidate-25", [str(candidates[-1].id)])
        ui.control("open")
        ui.assert_within_limits()

"""What `/build debug` hands a maintainer."""

import json

from squid.bot.submission.build_browse import _debug_dump
from squid.builds.domain import Status
from squid.builds.domain.credits import name_credits
from squid.builds.models import DoorBuild


def test_the_dump_is_json_a_person_can_read() -> None:
    """JSON serialization must produce enum names and ordinary values."""
    build = DoorBuild(id=42, submission_status=Status.PENDING, credits=name_credits(["Alice"], legacy=True))

    state = json.loads(_debug_dump(build))

    assert state["id"] == 42
    assert state["submission_status"] == "PENDING"
    assert state["creators_ign"] == ["Alice"]


def test_the_embedding_is_summarized_rather_than_dumped() -> None:
    """Report embedding size without letting thousands of floats obscure build metadata."""
    state = json.loads(_debug_dump(DoorBuild(id=42, embedding=[0.5] * 1536)))

    assert "embedding" not in state
    assert state["embedding_dimensions"] == 1536

    assert json.loads(_debug_dump(DoorBuild(id=42)))["embedding_dimensions"] is None

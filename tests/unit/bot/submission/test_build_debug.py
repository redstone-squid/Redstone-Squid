"""What `/build debug` hands a maintainer."""

import json

from squid.bot.submission.search import _debug_dump
from squid.builds.domain import DoorBuild, Status


def test_the_dump_is_json_a_person_can_read() -> None:
    """It was `str(build.__dict__)`, which renders enums as their repr and dicts as Python."""
    build = DoorBuild(id=42, submission_status=Status.PENDING, creators_ign=["Alice"])

    state = json.loads(_debug_dump(build))

    assert state["id"] == 42
    assert state["submission_status"] == "PENDING"
    assert state["creators_ign"] == ["Alice"]


def test_the_embedding_is_summarized_rather_than_dumped() -> None:
    """A few thousand floats would dominate the file and tell a reader nothing,
    but whether a build is embedded at all is a real question."""
    state = json.loads(_debug_dump(DoorBuild(id=42, embedding=[0.5] * 1536)))

    assert "embedding" not in state
    assert state["embedding_dimensions"] == 1536

    assert json.loads(_debug_dump(DoorBuild(id=42)))["embedding_dimensions"] is None

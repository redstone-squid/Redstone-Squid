"""Shared message snapshots retain partial drafts and their stored representation."""

import pytest
from pydantic import TypeAdapter
from whenever import Instant

from squid.messages.domain import MessageSnapshot
from squid.submissions.infrastructure.inference_payloads import BuildDraftPayload


def test_legacy_draft_source_messages_load_and_reserialize() -> None:
    adapter = TypeAdapter(BuildDraftPayload)
    draft = adapter.validate_python({"source_messages": [{"message_id": 123, "content": "original"}]})

    assert draft.source_messages == (MessageSnapshot(123, content="original"),)
    serialized = adapter.dump_python(draft, mode="json")
    assert serialized["source_messages"][0]["id"] == 123
    assert "message_id" not in serialized["source_messages"][0]
    assert adapter.validate_python(serialized).source_messages == draft.source_messages


def test_snapshot_timestamp_survives_draft_serialization() -> None:
    snapshot = MessageSnapshot(123, created_at=Instant.from_utc(2026, 11, 9))
    adapter = TypeAdapter(tuple[MessageSnapshot, ...])

    assert adapter.validate_json(adapter.dump_json((snapshot,))) == (snapshot,)


def test_partial_and_guild_snapshot_links() -> None:
    assert MessageSnapshot(123).link is None
    assert MessageSnapshot(123, channel_id=456, guild_id=789).link == "https://discord.com/channels/789/456/123"
    with pytest.raises(NotImplementedError, match="DMs"):
        _ = MessageSnapshot(123, channel_id=456).link

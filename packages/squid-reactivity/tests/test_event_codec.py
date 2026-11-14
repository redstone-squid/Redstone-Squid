"""Portable diagnostic records preserve their types and reject malformed JSON fields."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from squid_reactivity import (
    ActionContext,
    ActionResultSnapshot,
    ActorRef,
    CausalRef,
    ConflictDetail,
    ContinuationFailureSnapshot,
    EventCodec,
    ExceptionReport,
    OperationEventSnapshot,
    RedactionPolicy,
    ResourceEventSnapshot,
    transaction,
)
from squid_reactivity.diagnostics import CausalEventSnapshot


@pytest.fixture
def events() -> list[CausalEventSnapshot]:
    now = datetime.now(UTC)
    cause = CausalRef("action", "root")
    error = ExceptionReport("Error", "detail")
    context = ActionContext.create("edit", actor=ActorRef("user", "42"), metadata={"tenant": "example"})
    with transaction(action_context=context) as tx:
        pass
    with pytest.raises(ValueError), transaction("failed edit") as failed:
        raise ValueError("failure detail")
    assert tx.result is not None
    assert failed.result is not None
    events: list[CausalEventSnapshot] = [
        replace(
            ActionResultSnapshot.from_result(tx.result, RedactionPolicy(include_metadata=True)),
            tags=frozenset({"second", "first"}),
        ),
        replace(
            ActionResultSnapshot.from_result(failed.result, RedactionPolicy(include_exception_messages=True)),
            conflict=ConflictDetail("cell", 1, 2),
        ),
        OperationEventSnapshot("operation", "root", cause, "fetch", "failed", now, error),
        ResourceEventSnapshot("generation", "root", cause, "data", "failed", now, error),
        ContinuationFailureSnapshot("failure", "root", cause, "callback", "failed", now, error),
    ]
    return events


def test_event_codec_round_trips_every_variant(events: list[CausalEventSnapshot]) -> None:
    codec = EventCodec()
    assert [codec.decode(codec.encode(event)) for event in events] == events


@pytest.mark.parametrize(
    "data", [b"null", b"[]", b"invalid", b"\xff", b'{"schema":1}', b'{"schema":2,"event":"unknown","data":{}}']
)
def test_event_codec_rejects_unknown_or_malformed_records(data: bytes) -> None:
    with pytest.raises(ValueError, match="schema"):
        EventCodec().decode(data)


def test_event_codec_rejects_oversized_input() -> None:
    codec = EventCodec()
    with pytest.raises(ValueError, match="maximum encoded size"):
        codec.decode(b" " * (codec.max_encoded_bytes + 1))


@pytest.mark.parametrize("field", ["cause", "exception"])
def test_event_codec_rejects_incomplete_callback_failures(field: str) -> None:
    event = ContinuationFailureSnapshot(
        "failure",
        "root",
        CausalRef("action", "root"),
        "callback",
        "failed",
        datetime.now(UTC),
        ExceptionReport("Error", "detail"),
    )
    codec = EventCodec()
    payload = json.loads(codec.encode(event))
    payload["data"][field] = None
    with pytest.raises(ValueError, match="schema"):
        codec.decode(json.dumps(payload).encode())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("root_action_id", 42),
        ("timestamp", False),
        ("timestamp", "not a timestamp"),
        ("cause", []),
        ("cause", {"kind": "action", "identity": 42}),
        ("exception", {"type_name": "Error", "message": []}),
        ("exception", {"type_name": "Error"}),
        ("exception", {"type_name": "Error", "message": "detail", "extra": True}),
    ],
)
def test_event_codec_checks_shared_fields(events: list[CausalEventSnapshot], field: str, value: object) -> None:
    codec = EventCodec()
    for event in events:
        envelope = json.loads(codec.encode(event))
        envelope["data"][field] = value
        with pytest.raises(ValueError, match="schema"):
            codec.decode(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_id", None),
        ("kind", []),
        ("name", 42),
        ("terminal", True),
        ("reason", []),
        ("changes", {"cells": True, "participants": 0}),
        ("changes", {"cells": 1.5, "participants": 0}),
        ("changes", {"cells": 0, "participants": "0"}),
        ("sequence", {"runtime_id": "00000000-0000-0000-0000-000000000000", "value": False}),
        ("sequence", {"runtime_id": "invalid", "value": 1}),
        ("actor", {"kind": "user", "identity": 42}),
        ("conflict", {"target_id": "cell", "expected_version": "1", "actual_version": 2}),
        ("relation", {"kind": "reverses", "action_id": 42}),
        ("tags", "one-tag"),
        ("tags", [42]),
        ("metadata", {}),
        ("metadata", ["ab"]),
        ("metadata", [["key"]]),
        ("metadata", [["key", 42]]),
    ],
)
def test_event_codec_checks_action_fields(events: list[CausalEventSnapshot], field: str, value: object) -> None:
    codec = EventCodec()
    envelope = json.loads(codec.encode(events[0]))
    envelope["data"][field] = value
    with pytest.raises(ValueError, match="schema"):
        codec.decode(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("operation", ("execution_id", "name", "status")),
        ("resource", ("generation_id", "name", "status")),
        ("continuation_failure", ("failure_id", "stage", "callback")),
    ],
)
def test_event_codec_checks_variant_fields(
    events: list[CausalEventSnapshot], kind: str, fields: tuple[str, ...]
) -> None:
    codec = EventCodec()
    for event in events:
        envelope = json.loads(codec.encode(event))
        if envelope["event"] != kind:
            continue
        for field in fields:
            malformed = json.loads(codec.encode(event))
            malformed["data"][field] = 42
            with pytest.raises(ValueError, match="schema"):
                codec.decode(json.dumps(malformed).encode())


@pytest.mark.parametrize("missing", [True, False])
def test_event_codec_rejects_missing_or_unknown_fields(events: list[CausalEventSnapshot], missing: bool) -> None:
    codec = EventCodec()
    for event in events:
        envelope = json.loads(codec.encode(event))
        if missing:
            del envelope["data"]["timestamp"]
        else:
            envelope["data"]["unexpected"] = None
        with pytest.raises(ValueError, match="schema"):
            codec.decode(json.dumps(envelope).encode())


@pytest.mark.parametrize("schema", [True, 2.0, "2"])
def test_event_codec_requires_an_integer_schema(events: list[CausalEventSnapshot], schema: object) -> None:
    codec = EventCodec()
    envelope = json.loads(codec.encode(events[0]))
    envelope["schema"] = schema
    with pytest.raises(ValueError, match="schema"):
        codec.decode(json.dumps(envelope).encode())


def test_event_codec_rejects_excessive_json_nesting() -> None:
    with pytest.raises(ValueError, match="schema"):
        EventCodec().decode(b"[" * 10_000 + b"0" + b"]" * 10_000)

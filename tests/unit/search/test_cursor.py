"""Search cursor tests."""

import pytest

from squid.search.application import CursorCodec, InvalidCursorError
from squid.search.domain import CursorPosition, SearchMode, SearchRequest, SearchScope


def _position(request: SearchRequest, codec: CursorCodec) -> CursorPosition:
    return CursorPosition(
        query_hash=codec.request_hash(request),
        scope=request.scope,
        mode=request.mode,
        score=0.25,
        resource_kind="record",
        source_id="42",
    )


def test_cursor_round_trip_and_request_binding() -> None:
    codec = CursorCodec(b"a suitably long test secret")
    request = SearchRequest("title:door", scope=SearchScope.ALL, mode=SearchMode.SEMANTIC)
    position = _position(request, codec)

    assert codec.decode(codec.encode(position), request=request) == position


def test_cursor_rejects_tampering() -> None:
    codec = CursorCodec(b"a suitably long test secret")
    request = SearchRequest("door")
    encoded = codec.encode(_position(request, codec))
    replacement = "A" if encoded[-1] != "A" else "B"

    with pytest.raises(InvalidCursorError, match="signature"):
        codec.decode(encoded[:-1] + replacement)


def test_cursor_cannot_be_reused_for_another_request() -> None:
    codec = CursorCodec(b"a suitably long test secret")
    original = SearchRequest("door", scope=SearchScope.RECORDS)
    cursor = codec.encode(_position(original, codec))

    with pytest.raises(InvalidCursorError, match="different search request"):
        codec.decode(cursor, request=SearchRequest("door", scope=SearchScope.BUILDS))


def test_cursor_rejects_short_secret_and_garbage() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        CursorCodec(b"too short")

    with pytest.raises(InvalidCursorError):
        CursorCodec(b"a suitably long test secret").decode("not_base64!")

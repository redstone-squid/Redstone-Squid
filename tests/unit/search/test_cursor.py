"""Search cursor tests."""

import pytest

from squid.core.errors import ErrorCode, ValidationError
from squid.search.application import CursorCodec, InvalidCursorError
from squid.search.domain import CursorPosition, SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection


def _position(request: SearchRequest, codec: CursorCodec) -> CursorPosition:
    return CursorPosition(
        query_hash=codec.request_hash(request),
        scope=request.scope,
        mode=request.mode,
        score=0.25,
        resource_kind="record",
        source_id="42",
    )


def test_cursor_round_trip_and_request_binding(codec: CursorCodec) -> None:
    request = SearchRequest("title:door", scope=SearchScope.ALL, mode=SearchMode.SEMANTIC)
    position = _position(request, codec)

    assert codec.decode(codec.encode(position), request=request) == position


def test_cursor_rejects_tampering(codec: CursorCodec) -> None:
    request = SearchRequest("door")
    encoded = codec.encode(_position(request, codec))
    replacement = "A" if encoded[-1] != "A" else "B"

    with pytest.raises(InvalidCursorError, match="signature") as exc_info:
        codec.decode(encoded[:-1] + replacement)

    assert isinstance(exc_info.value, ValidationError)
    assert exc_info.value.code is ErrorCode.INVALID_CURSOR


def test_cursor_cannot_be_reused_for_another_request(codec: CursorCodec) -> None:
    original = SearchRequest("door", scope=SearchScope.RECORDS)
    cursor = codec.encode(_position(original, codec))

    with pytest.raises(InvalidCursorError, match="different search request"):
        codec.decode(cursor, request=SearchRequest("door", scope=SearchScope.BUILDS))


def test_cursor_cannot_be_reused_for_another_sort(codec: CursorCodec) -> None:
    original = SearchRequest("door", sort=SearchSort("width"))
    cursor = codec.encode(_position(original, codec))

    with pytest.raises(InvalidCursorError, match="different search request"):
        codec.decode(
            cursor,
            request=SearchRequest("door", sort=SearchSort("width", SortDirection.DESCENDING)),
        )


def test_cursor_cannot_be_reused_with_another_visibility_policy(codec: CursorCodec) -> None:
    original = SearchRequest("door", scope=SearchScope.BUILDS, visible_statuses=frozenset({"confirmed"}))
    cursor = codec.encode(_position(original, codec))

    with pytest.raises(InvalidCursorError, match="different search request"):
        codec.decode(
            cursor,
            request=SearchRequest("door", scope=SearchScope.BUILDS, visible_statuses=frozenset({"pending"})),
        )


def test_cursor_rejects_short_secret_and_garbage(codec: CursorCodec) -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        CursorCodec(b"too short")

    with pytest.raises(InvalidCursorError):
        codec.decode("not_base64!")

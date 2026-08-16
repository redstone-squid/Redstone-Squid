"""Request-Id correlation middleware tests."""

import re

import httpx
from starlette.datastructures import Headers

from squid.api.request_context import _traceparent_trace_id, resolve_request_id

_GENERATED = re.compile(r"[a-f0-9]{32}")
_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
_TRACEPARENT = f"00-{_TRACE_ID}-00f067aa0ba902b7-01"


def test_request_id_is_present_on_success(client: httpx.Client) -> None:
    response = client.get("/livez")

    assert _GENERATED.fullmatch(response.headers["Request-Id"])


def test_request_id_is_present_on_not_found(client: httpx.Client) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert _GENERATED.fullmatch(response.headers["Request-Id"])


def test_valid_inbound_request_id_is_echoed(client: httpx.Client) -> None:
    response = client.get("/livez", headers={"Request-Id": "my-request.id_01"})

    assert response.headers["Request-Id"] == "my-request.id_01"


def test_invalid_inbound_request_id_is_replaced(client: httpx.Client) -> None:
    for bad in ("short", "x" * 200, "has space", "tab\tinside"):
        response = client.get("/livez", headers={"Request-Id": bad})

        echoed = response.headers["Request-Id"]
        assert echoed != bad
        assert _GENERATED.fullmatch(echoed)


def test_traceparent_seeds_request_id_without_otel(client: httpx.Client) -> None:
    response = client.get("/livez", headers={"traceparent": _TRACEPARENT})

    assert response.headers["Request-Id"] == _TRACE_ID


def test_malformed_traceparent_falls_back_to_generated(client: httpx.Client) -> None:
    all_zero = f"00-{'0' * 32}-00f067aa0ba902b7-01"
    for value in ("not-a-traceparent", all_zero):
        response = client.get("/livez", headers={"traceparent": value})

        assert _GENERATED.fullmatch(response.headers["Request-Id"])


def test_resolve_request_id_prefers_valid_inbound() -> None:
    resolved = resolve_request_id(Headers({"Request-Id": "abcdefgh", "traceparent": _TRACEPARENT}))

    assert resolved == "abcdefgh"


def test_resolve_request_id_uses_traceparent_when_no_inbound_id() -> None:
    # Without the observability extra, active_trace_id() is None and traceparent is the seed.
    resolved = resolve_request_id(Headers({"traceparent": _TRACEPARENT}))

    assert resolved == _TRACE_ID


def test_resolve_request_id_generates_without_any_correlation() -> None:
    resolved = resolve_request_id(Headers({}))

    assert _GENERATED.fullmatch(resolved)


def test_traceparent_parsing_rejects_malformed_and_all_zero() -> None:
    assert _traceparent_trace_id(None) is None
    assert _traceparent_trace_id("garbage") is None
    assert _traceparent_trace_id(f"00-{'0' * 32}-00f067aa0ba902b7-01") is None
    assert _traceparent_trace_id(f"  {_TRACEPARENT}  ") == _TRACE_ID

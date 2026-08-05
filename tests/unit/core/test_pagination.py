"""Signed collection cursor tests."""

import pytest

from squid.core.errors import ErrorCode
from squid.core.pagination import InvalidPageCursorError, SignedCursor


def test_signed_cursor_round_trip_and_binding() -> None:
    signer = SignedCursor(b"shared-cursor-secret")
    token = signer.encode({"after_id": 42}, binding="builds:confirmed")

    assert signer.decode(token, binding="builds:confirmed") == {"after_id": 42}

    with pytest.raises(InvalidPageCursorError, match="different collection") as exc_info:
        signer.decode(token, binding="users:me:builds")
    assert exc_info.value.code is ErrorCode.INVALID_CURSOR


def test_signed_cursor_rejects_tampering_and_short_secrets() -> None:
    signer = SignedCursor(b"shared-cursor-secret")
    token = signer.encode({"after_id": 42}, binding="builds:confirmed")
    replacement = "A" if token[-1] != "A" else "B"

    with pytest.raises(InvalidPageCursorError):
        signer.decode(token[:-1] + replacement, binding="builds:confirmed")
    with pytest.raises(ValueError, match="16 bytes"):
        SignedCursor(b"short")

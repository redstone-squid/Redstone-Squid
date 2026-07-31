import pytest

from squid.search.application import CursorCodec

CURSOR_SECRET = b"a suitably long test secret"


@pytest.fixture
def codec() -> CursorCodec:
    return CursorCodec(CURSOR_SECRET)

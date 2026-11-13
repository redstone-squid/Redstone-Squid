"""Verification-code generation, digest construction, and service wiring.

Redemption looks up the code without a Minecraft UUID, so a guess can match any live
code. These tests cover code shape and keyed hashing; they do not measure resistance
to guessing or test the attempt limits.
"""

import hashlib
import hmac
import inspect

import pytest

from squid.accounts.application.services import generate_verification_code
from squid.accounts.infrastructure.repository import AccountRepository
from squid.bootstrap import _ServiceGraph

PEPPER = "pepper-for-tests"


def _repository() -> AccountRepository:
    # Hashing uses only the code and pepper, so these tests do not need a database session.
    return AccountRepository(None, PEPPER)  # type: ignore[arg-type]


def test_the_digest_is_a_keyed_hmac_not_a_prefixed_hash() -> None:
    digest = _repository().hash_verification_code("1234567890")

    assert digest == hmac.digest(PEPPER.encode(), b"1234567890", hashlib.sha256).hex()
    assert digest != hashlib.sha256(f"{PEPPER}1234567890".encode()).hexdigest()


def test_the_digest_depends_on_the_pepper() -> None:
    other = AccountRepository(None, "different-pepper")  # type: ignore[arg-type]

    assert other.hash_verification_code("1234567890") != _repository().hash_verification_code("1234567890")


@pytest.mark.parametrize("code", ["1000000000", "9999999999", "0", ""])
def test_the_digest_is_stable_and_hex(code: str) -> None:
    digest = _repository().hash_verification_code(code)

    assert digest == _repository().hash_verification_code(code)
    assert len(digest) == 64
    assert bytes.fromhex(digest)


@pytest.mark.parametrize(
    ("randbelow_result", "expected"),
    [(0, 1_000_000_000), (8_999_999_999, 9_999_999_999)],
)
def test_the_generated_code_covers_ten_digit_bounds(
    monkeypatch: pytest.MonkeyPatch,
    randbelow_result: int,
    expected: int,
) -> None:
    """Keep numeric codes compatible with the integer returned by the verification API."""
    requested_bounds: list[int] = []

    def fake_randbelow(bound: int) -> int:
        requested_bounds.append(bound)
        return randbelow_result

    monkeypatch.setattr("squid.accounts.application.services.secrets.randbelow", fake_randbelow)

    code = generate_verification_code()

    assert requested_bounds == [9_000_000_000]
    assert code == expected


def test_bootstrap_installs_the_widened_factory() -> None:
    """Check that bootstrap uses the tested generator rather than a separate implementation."""
    source = inspect.getsource(_ServiceGraph.accounts.func)  # type: ignore[attr-defined]

    assert "generate_verification_code" in source
    assert "900_000" not in source

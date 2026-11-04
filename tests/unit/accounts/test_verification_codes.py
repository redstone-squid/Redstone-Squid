"""Verification-code generation, digest construction, and service wiring.

Redemption looks up the code without a Minecraft UUID, so a guess can match any live
code. These tests cover code shape and keyed hashing; they do not measure resistance
to guessing or test the attempt limits.
"""

import hashlib
import hmac
import inspect

import pytest

from squid.accounts.application.services import VERIFICATION_CODE_DIGITS, generate_verification_code
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


def test_the_generated_code_is_ten_digits() -> None:
    """Keep numeric codes compatible with the integer returned by the verification API."""
    values = {generate_verification_code() for _ in range(2000)}

    assert all(10 ** (VERIFICATION_CODE_DIGITS - 1) <= value < 10**VERIFICATION_CODE_DIGITS for value in values)
    assert all(len(str(value)) == VERIFICATION_CODE_DIGITS for value in values)
    # This probabilistic smoke check can fail on a real random collision; it is not an entropy proof.
    assert len(values) == 2000


def test_bootstrap_installs_the_widened_factory() -> None:
    """Check that bootstrap uses the tested generator rather than a separate implementation."""
    source = inspect.getsource(_ServiceGraph.accounts.func)  # type: ignore[attr-defined]

    assert "generate_verification_code" in source
    assert "900_000" not in source

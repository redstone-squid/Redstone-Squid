"""Verification-code entropy and digest construction.

The code is the one credential in this codebase whose input is human-sized, and the redemption
never mentions the Minecraft UUID the code was issued for — so one guess is tested against every
outstanding code at once, and a hit attaches the matched Java account to whoever typed it. Entropy
and the attempt cap are the only levers; these tests pin the two that are pure functions.
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
    # The digest is a pure function of the pepper, so no session factory is exercised here.
    return AccountRepository(None, PEPPER)  # type: ignore[arg-type]


def test_the_digest_is_a_keyed_hmac_not_a_prefixed_hash() -> None:
    """Known-answer test against `hmac`, and a negative against the old construction.

    The prefixed form was not merely unfashionable: it is the weaker construction for no saving,
    and this asserts the change actually landed rather than being re-derived by hand somewhere.
    """
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
    """The real factory, not a copy of it, so widening cannot silently regress.

    Ten digits is about 33 bits against a ten-minute window. It stays numeric because `/verify`
    returns an `int` to the in-game plugin that shows the code to the player, so a base32 code
    would change that response type.
    """
    values = {generate_verification_code() for _ in range(2000)}

    assert all(10 ** (VERIFICATION_CODE_DIGITS - 1) <= value < 10**VERIFICATION_CODE_DIGITS for value in values)
    assert all(len(str(value)) == VERIFICATION_CODE_DIGITS for value in values)
    # Sanity check on the source of randomness: 2000 draws from 9e9 values should not repeat.
    assert len(values) == 2000


def test_bootstrap_installs_the_widened_factory() -> None:
    """Pins the wiring, because the entropy fix is worthless if bootstrap keeps its own lambda."""
    source = inspect.getsource(_ServiceGraph.accounts.func)  # type: ignore[attr-defined]

    assert "generate_verification_code" in source
    assert "900_000" not in source

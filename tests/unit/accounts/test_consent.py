"""The one Python spelling of the consent gate."""

from whenever import Instant

from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, consent_refresh_required

BEFORE_CUTOFF = Instant.from_utc(2026, 8, 3)
AFTER_CUTOFF = Instant.from_utc(2026, 8, 5)
SUPERSEDED = "1970-01-01"
"""Any receipt that is not the current one; spelled as a literal so bumping the live version
cannot quietly turn these cases into the "already current" case."""


def test_a_current_receipt_needs_no_refresh() -> None:
    assert not consent_refresh_required(AFTER_CUTOFF, CURRENT_CONSENT_VERSION)


def test_an_account_created_after_the_cutoff_must_consent() -> None:
    assert consent_refresh_required(AFTER_CUTOFF, None)


def test_an_account_predating_receipts_is_grandfathered() -> None:
    assert not consent_refresh_required(BEFORE_CUTOFF, None)


def test_the_cutoff_is_not_a_permanent_opt_out() -> None:
    """An account that has ever consented rejoins the version treadmill, cutoff or not.

    The cutoff means "predates receipts existing at all". Reading it as "exempt forever" is what
    let a pre-cutoff account skip every later notice, which is the bug this pins shut.
    """
    assert consent_refresh_required(BEFORE_CUTOFF, SUPERSEDED)
    assert consent_refresh_required(AFTER_CUTOFF, SUPERSEDED)


def test_an_unpersisted_account_is_never_grandfathered() -> None:
    """No creation instant means nothing to judge, so the safe answer is to ask."""
    assert consent_refresh_required(None, None)


def test_the_account_aggregate_delegates_to_the_same_predicate() -> None:
    grandfathered = Account(created_at=BEFORE_CUTOFF)
    stale = Account(created_at=AFTER_CUTOFF, consent=AccountConsent(SUPERSEDED, AFTER_CUTOFF))
    current = Account(created_at=AFTER_CUTOFF, consent=AccountConsent(CURRENT_CONSENT_VERSION, AFTER_CUTOFF))

    assert not grandfathered.needs_consent_refresh
    assert stale.needs_consent_refresh
    assert not current.needs_consent_refresh

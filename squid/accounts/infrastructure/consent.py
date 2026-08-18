"""The SQL spelling of the consent gate."""

from sqlalchemy import ColumnElement, and_, or_
from whenever import Instant

from squid.accounts.domain import CONSENT_CUTOFF, CURRENT_CONSENT_VERSION
from squid.accounts.infrastructure.models import Account

_CUTOFF = Instant.parse_iso(CONSENT_CUTOFF)
"""Parsed, because `created_at` binds through `InstantUTC` and will not take the raw string."""


def account_consent_current() -> ColumnElement[bool]:
    """Whether more data may be stored about the joined account.

    The SQL counterpart of `squid.accounts.domain.consent_refresh_required`, negated: this
    selects the accounts a write may proceed for. Both spellings exist because a session read
    already holds the row in Python while the notification queries filter thousands of rows in
    the database, and `tests/integration/accounts/test_consent_predicate.py` pins that they
    agree.

    Requires `Account` to be joined by the calling statement.
    """
    return or_(
        and_(
            Account.consent_version == CURRENT_CONSENT_VERSION,
            Account.consented_at.is_not(None),
        ),
        # Grandfathered only while the account has never consented to anything; see the domain
        # predicate for why the cutoff is not a permanent opt-out.
        and_(
            Account.consent_version.is_(None),
            Account.created_at.is_not(None),
            Account.created_at < _CUTOFF,
        ),
    )

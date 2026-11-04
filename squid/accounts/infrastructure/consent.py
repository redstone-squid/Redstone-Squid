"""The SQL spelling of the consent gate."""

from sqlalchemy import ColumnElement, and_, or_
from whenever import Instant

from squid.accounts.domain import CONSENT_CUTOFF, CURRENT_CONSENT_VERSION
from squid.accounts.models import Account

_CUTOFF = Instant.parse_iso(CONSENT_CUTOFF)
"""Parsed, because `created_at` binds through `InstantUTC` and will not take the raw string."""


def account_consent_current() -> ColumnElement[bool]:
    """Build a SQL predicate selecting accounts that pass the consent gate.

    Require a complete current receipt, or exempt accounts created before the
    cutoff that have never recorded consent. The statement must include Account.
    Integration tests compare this with the Python predicate for persisted rows.
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

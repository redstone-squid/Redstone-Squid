"""The privacy notice, the version that names it, and the receipt that records it."""

from dataclasses import dataclass

from whenever import Instant

from squid.core.i18n import tr

CURRENT_CONSENT_VERSION = "2026-08-18"

CONSENT_CUTOFF = "2026-08-04T00:00:00+00:00"
"""Accounts created before this instant predate consent receipts and are grandfathered."""

_CONSENT_CUTOFF_INSTANT = Instant.parse_iso(CONSENT_CUTOFF)
"""Parsed once: the predicate below runs on every authenticated request."""

PRIVACY_NOTICE_TITLE = tr(t"Privacy notice")

PRIVACY_NOTICE = tr(
    t"Redstone Squid stores your Discord user ID, plus your Minecraft UUID and username if you "
    t"link one, so it can recognise you across both.\n\n"
    t"Build credit under a name you verify is attributed to your account. Credit someone else "
    t"already holds is never taken from them; agreeing opens a claim for staff to review "
    t"instead.\n\n"
    t"Submitting a build publishes it. Its specifications, media and creator credit become part "
    t"of the public catalogue, are compared against other builds to compute records, and stay "
    t"listed after review. Schematics you attach are parsed, sanitised and rendered; whether one "
    t"is publicly downloadable, and under which licence, stays your choice on each "
    t"submission.\n\n"
    t"Your creator page is public by default. You can hide it, or any linked account on it; a "
    t"hidden page still lists the build credit you hold, because that credit is what attributes "
    t"the builds. Notifications are covered by this notice, but every channel stays off until "
    t"you turn it on.\n\n"
    t"Agreeing records this notice's version and the time. Cancelling stores nothing."
)
"""The notice shared by HTTP and Discord, translated as one message.

Keep the notice beside its version so receipts identify the text users accepted.
Licensing is chosen per schematic submission; the notice describes processing
without choosing a licence on the submitter's behalf.
"""


@dataclass(frozen=True, slots=True)
class AccountConsent:
    """Evidence that an account accepted a particular privacy notice."""

    version: str
    granted_at: Instant

    @classmethod
    def grant_current(cls) -> AccountConsent:
        """Create a receipt for the currently published privacy notice."""
        return cls(version=CURRENT_CONSENT_VERSION, granted_at=Instant.now())


def consent_refresh_required(created_at: Instant | None, consent_version: str | None) -> bool:
    """Return whether an account must accept the current privacy notice.

    An account created before CONSENT_CUTOFF is exempt only while it has no receipt.
    Once it accepts a notice, it must keep that receipt current like newer accounts.
    An account without a creation time is never exempt.

    The SQL counterpart in accounts.infrastructure.consent selects accounts that
    pass this gate. Integration tests check agreement for persisted accounts.
    """
    if consent_version == CURRENT_CONSENT_VERSION:
        return False
    if consent_version is not None:
        return True
    return created_at is None or created_at >= _CONSENT_CUTOFF_INSTANT

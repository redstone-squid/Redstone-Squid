"""The privacy notice, the version that names it, and the receipt that records it."""

from dataclasses import dataclass

from whenever import Instant

from squid.core.i18n import _

CURRENT_CONSENT_VERSION = "2026-08-04"

CONSENT_CUTOFF = "2026-08-04T00:00:00+00:00"
"""Accounts created before this instant predate consent receipts and are grandfathered."""

_CONSENT_CUTOFF_INSTANT = Instant.parse_iso(CONSENT_CUTOFF)
"""Parsed once: the predicate below runs on every authenticated request."""

PRIVACY_NOTICE_TITLE = _("Privacy notice")

PRIVACY_NOTICE = _(
    "Redstone Squid stores your Discord user ID, your Minecraft UUID and your current Minecraft "
    "username. The pair is what lets the bot recognise you as a build creator and keep the two "
    "accounts associated.\n\n"
    "Linking also claims build credit recorded under your verified Minecraft username, so those "
    "builds are attributed to your account. Credit already claimed by someone else is never taken "
    "from them; agreeing opens a claim for staff to review instead.\n\n"
    "Every account has a public creator page, and a linked account appears on yours by default. "
    "You can hide any linked account individually with `/account visibility`, or hide the whole "
    "page. A hidden page still lists the build credit you hold, because that credit is what "
    "attributes the builds themselves.\n\n"
    "Agreeing records which version of this notice you accepted and when. Cancelling stores no "
    "account information at all."
)
"""The full notice, served over HTTP and shown behind a button in Discord.

One message rather than several so the version recorded in a consent receipt refers to a single
piece of text. It lives in the domain beside the version that names it, because splitting the two
is how they drift; every transport renders this same msgid in the caller's locale.

`CURRENT_CONSENT_VERSION` is deliberately *not* bumped for a change that only clarifies. The
version discipline starts at the first alpha, and a change that widens what is stored or published
needs a bump after that point.
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
    """Whether the current notice must be accepted before more data about this account is stored.

    The one Python spelling of the gate, taking raw columns because the browser-session reader
    holds those rather than an assembled `Account`. `squid.accounts.infrastructure.consent`
    carries the SQL spelling, and a test pins that the two agree.

    Grandfathering is deliberately narrow. `CONSENT_CUTOFF` means "predates receipts existing at
    all", not "opted out permanently", so an account that has *ever* consented rejoins the version
    treadmill even if it predates the cutoff. An unpersisted account has no creation instant to
    judge and is never grandfathered.
    """
    if consent_version == CURRENT_CONSENT_VERSION:
        return False
    if consent_version is not None:
        return True
    return created_at is None or created_at >= _CONSENT_CUTOFF_INSTANT

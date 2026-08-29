"""The privacy notice, the version that names it, and the receipt that records it."""

from dataclasses import dataclass

from whenever import Instant

from squid.core.i18n import _

CURRENT_CONSENT_VERSION = "2026-08-18"

CONSENT_CUTOFF = "2026-08-04T00:00:00+00:00"
"""Accounts created before this instant predate consent receipts and are grandfathered."""

_CONSENT_CUTOFF_INSTANT = Instant.parse_iso(CONSENT_CUTOFF)
"""Parsed once: the predicate below runs on every authenticated request."""

PRIVACY_NOTICE_TITLE = _("Privacy notice")

PRIVACY_NOTICE = _(
    "Redstone Squid stores your Discord user ID, plus your Minecraft UUID and username if you "
    "link one, so it can recognise you across both.\n\n"
    "Build credit under a name you verify is attributed to your account. Credit someone else "
    "already holds is never taken from them; agreeing opens a claim for staff to review "
    "instead.\n\n"
    "Submitting a build publishes it. Its specifications, media and creator credit become part "
    "of the public catalogue, are compared against other builds to compute records, and stay "
    "listed after review. Schematics you attach are parsed, sanitised and rendered; whether one "
    "is publicly downloadable, and under which licence, stays your choice on each "
    "submission.\n\n"
    "Your creator page is public by default. You can hide it, or any linked account on it; a "
    "hidden page still lists the build credit you hold, because that credit is what attributes "
    "the builds. Notifications are covered by this notice, but every channel stays off until "
    "you turn it on.\n\n"
    "Agreeing records this notice's version and the time. Cancelling stores nothing."
)
"""The full notice, served over HTTP and shown behind a button in Discord.

One message rather than several so the version recorded in a consent receipt refers to a single
piece of text. It lives in the domain beside the version that names it, because splitting the two
is how they drift; every transport renders this same msgid in the caller's locale.

It names no command. The notice is served over HTTP as well as in Discord, and the one command
name it used to carry outlived the command (`/account visibility`, folded into the `/account`
panel in phase 7). Dropping it changed no fact the receipt covers, so `CURRENT_CONSENT_VERSION`
did not move -- a bump asks every user to re-accept, and spending that on a cross-reference is
how the version stops meaning anything.

It describes submission as well as storage because it now fronts many actions rather than one.
The submission paragraph deliberately *defers* on licensing rather than stating terms: schematics
already carry a per-submission licence choice (`SubmissionSchematicLicense`), and build text and
media carry none anywhere in the codebase, so claiming terms for them here would be inventing
them. What it does say is what actually happens.
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

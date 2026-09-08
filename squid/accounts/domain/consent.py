"""The privacy notice, the version that names it, and the receipt that records it."""

from dataclasses import dataclass

from whenever import Instant

from squid.core.i18n import tr

CURRENT_CONSENT_VERSION = "2026-08-18"
"""Bumping this asks every account to accept the notice again, so only move it when the facts the
receipt covers change."""

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
"""The full notice, served over HTTP and shown behind a button in Discord.

One message rather than several, so the version in a receipt names a single piece of text; it sits
beside `CURRENT_CONSENT_VERSION` so the two cannot drift. Every transport renders this msgid in the
caller's locale, so it names no command. The submission paragraph defers on licensing rather than
stating terms: schematics carry a per-submission licence choice (`SubmissionSchematicLicense`) and
build text and media carry none, so terms stated here would be invented.
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

    Takes raw columns because the browser-session reader holds those rather than an assembled
    `Account`. `squid.accounts.infrastructure.consent` carries the SQL spelling of the same gate,
    and a test pins that the two agree.

    Grandfathering is narrow: an account that has ever consented is judged on its version even if it
    predates `CONSENT_CUTOFF`, and an unpersisted account (`created_at is None`) is never
    grandfathered.
    """
    if consent_version == CURRENT_CONSENT_VERSION:
        return False
    if consent_version is not None:
        return True
    return created_at is None or created_at >= _CONSENT_CUTOFF_INSTANT

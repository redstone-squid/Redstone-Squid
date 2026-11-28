"""The privacy notice, the version that names it, and the receipt that records it."""

from dataclasses import dataclass

from whenever import Instant

from squid.core.i18n import tr

PRIVACY_NOTICE_VERSION = "2026-11-28"

PRIVACY_NOTICE_TITLE = tr(t"accounts-domain-consent-privacy-notice = Privacy notice")

PRIVACY_NOTICE = tr(t"""
    accounts-discord-user-id-stored =
        Redstone Squid stores your Discord user ID, plus your Minecraft UUID and username if you link one, so it can recognise you across both.

        Published builds keep the names their submitters credited. You can request attribution for individual build credits. Staff review requests and account nominations. Verifying a username does not claim past builds under that name.

        Submitting a build publishes it. Its specifications, media and creator credit become part of the public catalogue, are compared against other builds to compute records, and stay listed after review. Schematics you attach are parsed, sanitised and rendered; whether one is publicly downloadable, and under which licence, stays your choice on each submission.

        Your creator page is public by default. You can hide it, or any linked account on it; published credit names remain searchable when you hide your profile. Notifications are covered by this notice, but every channel stays off until you turn it on.

        Automatic indexing is off by default. If you turn it on, Redstone Squid adds your builds from participating Discord channels to the catalogue and copies their attachments. Turning it off stops future indexing. Builds already in the catalogue stay there.
    """)
"""The notice shared by HTTP and Discord, translated as one message.

Keep the notice beside its version so receipts identify the text users accepted.
Licensing is chosen per schematic submission; the notice describes processing
without choosing a licence on the submitter's behalf.
"""


@dataclass(frozen=True, slots=True)
class PrivacyNoticeReceipt:
    """Evidence that an account accepted a particular privacy notice."""

    version: str
    granted_at: Instant

    @classmethod
    def grant_current(cls) -> PrivacyNoticeReceipt:
        """Create a receipt for the currently published privacy notice."""
        return cls(version=PRIVACY_NOTICE_VERSION, granted_at=Instant.now())

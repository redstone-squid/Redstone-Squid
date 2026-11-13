"""Shared presentation for Minecraft identity reconciliation."""

from collections.abc import Sequence
from uuid import UUID

from squid.accounts.domain import IdentityRefresh, LinkPreview
from squid.accounts.models import AccountIdentity
from squid_i18n import Text, md_tr
from squid_ui.text import md


def link_conflict(preview: LinkPreview, existing_java: Sequence[AccountIdentity]) -> UUID | None:
    """Return the first linked UUID that blocks this link, in persistence order."""
    if mismatched := next((identity for identity in existing_java if identity.java_uuid != preview.java_uuid), None):
        return mismatched.java_uuid
    if preview.java_uuid_held_elsewhere and not existing_java:
        return preview.java_uuid
    return None


def link_message(refresh: IdentityRefresh) -> Text:
    """Render a link outcome with the same reconciliation details as a refresh."""
    name = refresh.current_name
    return Text.join(
        "\n",
        [
            md_tr(t"account-discord-linked = Your Discord account is now linked to **{name}**."),
            *reconciliation_lines(refresh),
        ],
    )


def refresh_message(refresh: IdentityRefresh) -> Text:
    """Render every branch of a refresh, including the one where nothing changed."""
    if not refresh.renamed:
        name = refresh.current_name
        headline = md_tr(t"account-name-unchanged = Your Minecraft name is still **{name}**. Nothing changed.")
    else:
        old = refresh.previous_name
        new = refresh.current_name
        headline = md_tr(t"account-name-changed = Your Minecraft name changed from **{old}** to **{new}**.")
    return Text.join("\n", [headline, *reconciliation_lines(refresh)])


def reconciliation_lines(refresh: IdentityRefresh) -> list[Text]:
    """Describe creator-credit reconciliation shared by linking and refreshing."""
    lines: list[Text] = []
    if refresh.claimed_alias is not None:
        name = refresh.claimed_alias.name
        lines.append(
            md_tr(
                t"account-credits-attributed = Build credits under **{name}** are attributed to your account.",
            )
        )
    elif refresh.contested_alias is not None:
        name = refresh.contested_alias.name
        id = refresh.opened_claim.id if refresh.opened_claim is not None else 0
        lines.append(
            md_tr(
                t"account-claim-awaiting-review = **{name}** is already "
                t"credited to another account, so it was not moved. Claim #{id!s} is awaiting staff review.",
            )
        )

    if refresh.retained_alias_names:
        # Escape each name; only the bold markers around it are trusted Markdown.
        names = Text.join(", ", (md(t"**{name}**") for name in refresh.retained_alias_names))
        lines.append(md_tr(t"account-credits-retained = You are still credited under: {names}."))
    return lines

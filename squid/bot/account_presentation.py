"""Shared presentation for Minecraft identity reconciliation."""

from collections.abc import Sequence
from uuid import UUID

from squid.accounts.domain import IdentityRefresh, LinkPreview
from squid.accounts.models import AccountIdentity
from squid.core.i18n import tr
from squid_ui.text import Message, md


def link_conflict(preview: LinkPreview, existing_java: Sequence[AccountIdentity]) -> UUID | None:
    """Return the first linked UUID that blocks this link, in persistence order."""
    if mismatched := next((identity for identity in existing_java if identity.java_uuid != preview.java_uuid), None):
        return mismatched.java_uuid
    if preview.java_uuid_held_elsewhere and not existing_java:
        return preview.java_uuid
    return None


def link_message(refresh: IdentityRefresh) -> Message:
    """Render a link outcome with the same reconciliation details as a refresh."""
    name = refresh.current_name
    return Message.join(
        "\n", [tr(t"Your Discord account is now linked to **{name}**."), *reconciliation_lines(refresh)]
    )


def refresh_message(refresh: IdentityRefresh) -> Message:
    """Render every branch of a refresh, including the one where nothing changed."""
    if not refresh.renamed:
        name = refresh.current_name
        headline = tr(t"Your Minecraft name is still **{name}**. Nothing changed.")
    else:
        old = refresh.previous_name
        new = refresh.current_name
        headline = tr(t"Your Minecraft name changed from **{old}** to **{new}**.")
    return Message.join("\n", [headline, *reconciliation_lines(refresh)])


def reconciliation_lines(refresh: IdentityRefresh) -> list[Message]:
    """Describe creator-credit reconciliation shared by linking and refreshing."""
    lines: list[Message] = []
    if refresh.claimed_alias is not None:
        name = refresh.claimed_alias.name
        lines.append(tr(t"Build credits under **{name}** are attributed to your account."))
    elif refresh.contested_alias is not None:
        name = refresh.contested_alias.name
        id = refresh.opened_claim.id if refresh.opened_claim is not None else 0
        lines.append(
            tr(
                t"**{name}** is already credited to another account, so it was not moved. "
                t"Claim #{id} is awaiting staff review."
            )
        )

    if refresh.retained_alias_names:
        # Escape each name; only the bold markers around it are trusted Markdown.
        names = Message.join(", ", (md(t"**{name}**") for name in refresh.retained_alias_names))
        lines.append(tr(t"You are still credited under: {names}."))
    return lines

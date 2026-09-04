"""Shared presentation for Minecraft identity reconciliation."""

from collections.abc import Sequence
from uuid import UUID

from squid.accounts.domain import AccountIdentity, IdentityRefresh, LinkPreview
from squid.core.i18n import tr
from squid_ui.text import raw_md


def link_conflict(preview: LinkPreview, existing_java: Sequence[AccountIdentity]) -> UUID | None:
    """Return the first linked UUID that blocks this link, in persistence order."""
    if mismatched := next((identity for identity in existing_java if identity.java_uuid != preview.java_uuid), None):
        return mismatched.java_uuid
    if preview.java_uuid_held_elsewhere and not existing_java:
        return preview.java_uuid
    return None


def link_message(refresh: IdentityRefresh) -> str:
    """Render a link outcome with the same reconciliation details as a refresh."""
    name = refresh.current_name
    lines = [tr(tr(t"Your Discord account is now linked to **{name}**."))]
    lines.extend(reconciliation_lines(refresh))
    return "\n".join(lines)


def refresh_message(refresh: IdentityRefresh) -> str:
    """Render every branch of a refresh, including the one where nothing changed."""
    name = refresh.current_name
    if not refresh.renamed:
        lines = [tr(tr(t"Your Minecraft name is still **{name}**. Nothing changed."))]
    else:
        old = refresh.previous_name
        new = refresh.current_name
        lines = [tr(tr(t"Your Minecraft name changed from **{old}** to **{new}**."))]
    lines.extend(reconciliation_lines(refresh))
    return "\n".join(lines)


def reconciliation_lines(refresh: IdentityRefresh) -> list[str]:
    """Describe creator-credit reconciliation shared by linking and refreshing."""
    lines: list[str] = []
    if refresh.claimed_alias is not None:
        name = refresh.claimed_alias.name
        lines.append(tr(tr(t"Build credits under **{name}** are attributed to your account.")))
    elif refresh.contested_alias is not None:
        name = refresh.contested_alias.name
        id = refresh.opened_claim.id if refresh.opened_claim is not None else 0
        lines.append(
            tr(
                tr(
                    t"**{name}** is already credited to another account, so it was not moved. "
                    t"Claim #{id} is awaiting staff review."
                )
            )
        )

    if refresh.retained_alias_names:
        names = raw_md(", ".join(f"**{name}**" for name in refresh.retained_alias_names))
        lines.append(tr(tr(t"You are still credited under: {names}.")))
    return lines

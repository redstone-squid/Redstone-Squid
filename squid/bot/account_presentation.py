"""Shared presentation for Minecraft identity reconciliation."""

from collections.abc import Sequence
from uuid import UUID

from squid.accounts.domain import AccountIdentity, IdentityRefresh, LinkPreview
from squid.core.i18n import tr


def link_conflict(preview: LinkPreview, existing_java: Sequence[AccountIdentity]) -> UUID | None:
    """Return the first linked UUID that blocks this link, in persistence order."""
    if mismatched := next((identity for identity in existing_java if identity.java_uuid != preview.java_uuid), None):
        return mismatched.java_uuid
    if preview.java_uuid_held_elsewhere and not existing_java:
        return preview.java_uuid
    return None


def link_message(refresh: IdentityRefresh) -> str:
    """Render a link outcome with the same reconciliation details as a refresh."""
    lines = [tr("Your Discord account is now linked to **{name}**.", name=refresh.current_name)]
    lines.extend(reconciliation_lines(refresh))
    return "\n".join(lines)


def refresh_message(refresh: IdentityRefresh) -> str:
    """Render every branch of a refresh, including the one where nothing changed."""
    if not refresh.renamed:
        lines = [tr("Your Minecraft name is still **{name}**. Nothing changed.", name=refresh.current_name)]
    else:
        lines = [
            tr(
                "Your Minecraft name changed from **{old}** to **{new}**.",
                old=refresh.previous_name,
                new=refresh.current_name,
            )
        ]
    lines.extend(reconciliation_lines(refresh))
    return "\n".join(lines)


def reconciliation_lines(refresh: IdentityRefresh) -> list[str]:
    """Describe creator-credit reconciliation shared by linking and refreshing."""
    lines: list[str] = []
    if refresh.claimed_alias is not None:
        lines.append(
            tr(
                "Build credits under **{name}** are attributed to your account.",
                name=refresh.claimed_alias.name,
            )
        )
    elif refresh.contested_alias is not None:
        lines.append(
            tr(
                "**{name}** is already credited to another account, so it was not moved. "
                "Claim #{id} is awaiting staff review.",
                name=refresh.contested_alias.name,
                id=refresh.opened_claim.id if refresh.opened_claim is not None else 0,
            )
        )

    if refresh.retained_alias_names:
        lines.append(
            tr(
                "You are still credited under: {names}.",
                names=", ".join(f"**{name}**" for name in refresh.retained_alias_names),
            )
        )
    return lines

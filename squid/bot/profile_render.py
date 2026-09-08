"""Creator profile card fields shared by the caller's own view and the public view.

One field layout for both, so an `AccountProfile` and a `PublicCreatorProfile` of the same account
render identically.
"""

from squid.accounts.domain import (
    AccountIdentity,
    AccountProfile,
    AliasClaim,
    IdentityProvider,
    ProfileLink,
    PublicCreatorProfile,
    avatar_url_for,
)
from squid.bot.ui import CardField, tr

_PROVIDER_LABELS = {
    IdentityProvider.DISCORD: tr(t"Discord"),
    IdentityProvider.JAVA: tr(t"Minecraft (Java)"),
    IdentityProvider.BEDROCK: tr(t"Minecraft (Bedrock)"),
}


def provider_label(provider: IdentityProvider) -> str:
    return tr(_PROVIDER_LABELS[provider])


def identity_label(identity: AccountIdentity) -> str:
    """A Discord identity renders as a mention; others as display name, falling back to the subject."""
    provider = provider_label(identity.provider)
    if identity.provider is IdentityProvider.DISCORD and identity.discord_id is not None:
        return tr("{provider} — <@{subject}>", provider=provider, subject=identity.discord_id)
    return tr(
        "{provider} — {name}",
        provider=provider,
        name=identity.display_name or identity.subject,
    )


def render_links(links: tuple[ProfileLink, ...]) -> str:
    return "\n".join(f"[{link.label}]({link.url})" for link in links)


def own_profile_fields(profile: AccountProfile) -> list[CardField]:
    """Pronouns and links only; the account panel lists linked identities itself, one field each with controls."""
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(tr("Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr("Links"), render_links(profile.links)))
    return fields


def own_profile_avatar(profile: AccountProfile, identities: tuple[AccountIdentity, ...]) -> tuple[str, ...]:
    """Zero or one avatar URL; ignores the identity's `is_public` since the viewer is the owner."""
    source = next((identity for identity in identities if identity.id == profile.avatar_identity_id), None)
    if source is None:
        return ()
    url = avatar_url_for(source)
    return () if url is None else (url,)


def public_profile_fields(profile: PublicCreatorProfile) -> list[CardField]:
    """Does no visibility filtering: `PublicCreatorProfile` arrives already filtered, same as the API's."""
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(tr("Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr("Links"), render_links(profile.links)))
    if profile.identities:
        fields.append(
            CardField(
                tr("Linked accounts"),
                "\n".join(
                    tr(
                        "{provider} — {name}",
                        provider=provider_label(identity.provider),
                        name=identity.display_name or identity.subject,
                    )
                    for identity in profile.identities
                ),
            )
        )
    if profile.aliases:
        fields.append(
            CardField(
                tr("Creator credit"),
                "\n".join(
                    tr(
                        "**{name}** — {count} build(s)",
                        name=alias.name,
                        count=alias.build_count,
                    )
                    for alias in profile.aliases
                ),
            )
        )
    return fields


def present_claimant(claim: AliasClaim, *, mention: bool = True) -> str:
    """Name a claimant: Discord mention, then Java name, then creator id, then Discord id, then internal account id.

    Used by every surface that shows a claimant. `mention=False` is for select option descriptions,
    where Discord renders `<@id>` as raw text; the Discord id then drops to a diagnostic near the end.
    """
    claimant = claim.claimant
    if claimant is not None:
        discord = claimant.identity(IdentityProvider.DISCORD)
        if mention and discord is not None and discord.discord_id is not None:
            return f"<@{discord.discord_id}>"
        java = claimant.identity(IdentityProvider.JAVA)
        if java is not None and java.display_name is not None:
            return java.display_name
        if claimant.public_creator_id is not None:
            return tr("creator `{creator_id}`", creator_id=claimant.public_creator_id)
        if discord is not None and discord.discord_id is not None:
            return tr("Discord user `{discord_id}`", discord_id=discord.discord_id)
    return tr("unidentified account (internal ID `{account_id}`)", account_id=claim.account_id)

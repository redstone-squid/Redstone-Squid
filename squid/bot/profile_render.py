"""Rendering for creator profiles in Discord.

Kept out of `verify.py` because the same card is built from two different inputs: the caller's own
profile, which shows hidden fields, and a stranger's, which has already been filtered by
`present_public_profile`. Sharing the field layout here means the two views cannot drift into
describing the same profile differently.
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
    """Name a provider the way a player would."""
    return tr(_PROVIDER_LABELS[provider])


def identity_label(identity: AccountIdentity) -> str:
    """Name one identity for a picker or a list.

    A Discord identity renders as a mention, which is the only handle a reader can click; every
    other provider has a stored display name worth showing, and falls back to the subject.
    """
    provider = provider_label(identity.provider)
    if identity.provider is IdentityProvider.DISCORD and identity.discord_id is not None:
        return tr("{provider} — <@{subject}>", provider=provider, subject=identity.discord_id)
    return tr(
        "{provider} — {name}",
        provider=provider,
        name=identity.display_name or identity.subject,
    )


def render_links(links: tuple[ProfileLink, ...]) -> str:
    """Render links as markdown, one per line."""
    return "\n".join(f"[{link.label}]({link.url})" for link in links)


def own_profile_fields(profile: AccountProfile) -> list[CardField]:
    """Describe the free-text half of the caller's own profile.

    The linked accounts are not here, unlike in the public view: the panel that renders this lists
    them one field each, because it also has to say whether each is published and offer the
    controls for it (docs/plans/command-redesign/07-account.md).
    """
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(tr("Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr("Links"), render_links(profile.links)))
    return fields


def own_profile_avatar(profile: AccountProfile, identities: tuple[AccountIdentity, ...]) -> tuple[str, ...]:
    """Return the avatar to show on the caller's own card, if one resolves.

    Unlike the public view this ignores `is_public`: you are looking at your own profile, and
    hiding the identity from strangers is not a reason to hide your own avatar from you.
    """
    source = next((identity for identity in identities if identity.id == profile.avatar_identity_id), None)
    if source is None:
        return ()
    url = avatar_url_for(source)
    return () if url is None else (url,)


def public_profile_fields(profile: PublicCreatorProfile) -> list[CardField]:
    """Describe what a stranger may see.

    Takes an already-filtered `PublicCreatorProfile` rather than filtering here, so the bot and
    the API cannot disagree about visibility.
    """
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
    """Name a claimant by the most recognisable identity loaded for them.

    One function for every surface that shows a claimant — the review queue, its select, an
    approval and a rejection — so a reviewer reads the same thing everywhere.

    A Discord mention is preferred because it is the only handle a reviewer can click, and because a
    Discord identity never gets a stored `display_name`; Discord resolves the snowflake client-side.
    `mention=False` is for the places Discord renders no chip — a select option's description shows
    `<@id>` as raw text — and falls back to the names a reviewer can actually read. The internal
    account ID is last and is labelled as a diagnostic, since it identifies a row rather than a
    person.
    """
    claimant = claim.claimant
    if claimant is not None:
        discord = claimant.most_recent_identity_for(IdentityProvider.DISCORD)
        if mention and discord is not None and discord.discord_id is not None:
            return f"<@{discord.discord_id}>"
        java = claimant.most_recent_identity_for(IdentityProvider.JAVA)
        if java is not None and java.display_name is not None:
            return java.display_name
        if claimant.public_creator_id is not None:
            return tr("creator `{creator_id}`", creator_id=claimant.public_creator_id)
        if discord is not None and discord.discord_id is not None:
            # Reached only without a mention: the snowflake is the last handle left, and it is a
            # diagnostic here rather than a name.
            return tr("Discord user `{discord_id}`", discord_id=discord.discord_id)
    return tr("unidentified account (internal ID `{account_id}`)", account_id=claim.account_id)

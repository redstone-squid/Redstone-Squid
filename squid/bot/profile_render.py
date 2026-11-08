"""Render identity labels and fields for private and public creator profiles.

Public rendering accepts the account service's already-filtered view. Private
rendering can include the caller's hidden identities and avatar.
"""

from squid.accounts.domain import IdentityProvider, ProfileLink, PublicCreatorProfile, PublicIdentity, avatar_url_for
from squid.accounts.models import AccountIdentity, AccountProfile, AliasClaim
from squid.bot.ui import CardField, tr
from squid_ui.text import Message, raw_md

_PROVIDER_LABELS = {
    IdentityProvider.DISCORD: tr(t"Discord"),
    IdentityProvider.JAVA: tr(t"Minecraft (Java)"),
    IdentityProvider.BEDROCK: tr(t"Minecraft (Bedrock)"),
}


def provider_label(provider: IdentityProvider) -> Message:
    """Name a provider the way a player would."""
    return _PROVIDER_LABELS[provider]


def identity_label(identity: AccountIdentity) -> Message:
    """Name one identity for a picker or a list.

    A Discord identity renders as a mention, which is the only handle a reader can click; every
    other provider has a stored display name worth showing, and falls back to the subject.
    """
    provider = provider_label(identity.provider)
    if identity.provider is IdentityProvider.DISCORD and identity.discord_id is not None:
        subject = identity.discord_id
        return tr(t"{provider} — <@{subject}>")
    name = identity.display_name or identity.subject
    return tr(t"{provider} — {name}")


def render_links(links: tuple[ProfileLink, ...]) -> str:
    """Render links as markdown, one per line."""
    return "\n".join(f"[{link.label}]({link.url})" for link in links)


def own_profile_fields(profile: AccountProfile) -> list[CardField]:
    """Render pronouns and links; the account panel renders identity visibility separately."""
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(tr(t"Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr(t"Links"), render_links(profile.links)))
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
        fields.append(CardField(tr(t"Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr(t"Links"), render_links(profile.links)))
    if profile.identities:
        fields.append(
            CardField(
                tr(t"Linked accounts"),
                Message.join("\n", (_public_identity_label(identity) for identity in profile.identities)),
            )
        )
    if profile.aliases:
        fields.append(
            CardField(
                tr(t"Creator credit"),
                Message.join("\n", (_alias_credit_label(alias.name, alias.build_count) for alias in profile.aliases)),
            )
        )
    return fields


def _public_identity_label(identity: PublicIdentity) -> Message:
    provider = provider_label(identity.provider)
    name = identity.display_name or identity.subject
    return tr(t"{provider} — {name}")


def _alias_credit_label(name: str, count: int) -> Message:
    return tr(t"**{name}** — {count} build", plural=t"**{name}** — {count} builds")


def present_claimant(claim: AliasClaim, *, mention: bool = True) -> str:
    """Choose a readable claimant label from the identities loaded with the claim.

    Prefer a Discord mention when allowed, then a Java name, public creator ID,
    Discord ID, and finally the internal account ID. Disable mentions for select
    descriptions, where Discord would display the raw mention syntax.

    Returns a string because autocomplete and select descriptions truncate it.
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
            creator_id = raw_md(claimant.public_creator_id)
            return tr(t"creator `{creator_id}`").resolve()
        if discord is not None and discord.discord_id is not None:
            # Reached only without a mention: the snowflake is the last handle left, and it is a
            # diagnostic here rather than a name.
            discord_id = raw_md(discord.discord_id)
            return tr(t"Discord user `{discord_id}`").resolve()
    account_id = raw_md(claim.account_id)
    return tr(t"unidentified account (internal ID `{account_id}`)").resolve()

"""Render identity labels and fields for private and public creator profiles.

Public rendering accepts the account service's already-filtered view. Private
rendering can include the caller's hidden identities and avatar.
"""

from squid.accounts.application.presentation import claimant_label
from squid.accounts.domain import IdentityProvider, ProfileLink, PublicCreatorProfile, PublicIdentity, avatar_url_for
from squid.accounts.models import AccountIdentity, AccountProfile, AliasClaim
from squid.bot.ui import CardField, tr
from squid_ui.text import Message

_PROVIDER_LABELS = {
    IdentityProvider.DISCORD: tr(t"bot-profile-render-discord = Discord"),
    IdentityProvider.JAVA: tr(t"bot-profile-render-minecraft-java = Minecraft (Java)"),
    IdentityProvider.BEDROCK: tr(t"bot-profile-render-minecraft-bedrock = Minecraft (Bedrock)"),
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
        subject = str(identity.discord_id)
        return tr(t"bot-profile-render-value-2 = {provider} — <@{subject}>")
    name = identity.display_name or identity.subject
    return tr(t"bot-profile-render-value = {provider} — {name}")


def render_links(links: tuple[ProfileLink, ...]) -> str:
    """Render links as markdown, one per line."""
    return "\n".join(f"[{link.label}]({link.url})" for link in links)


def own_profile_fields(profile: AccountProfile) -> list[CardField]:
    """Render pronouns and links; the account panel renders identity visibility separately."""
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(tr(t"bot-profile-render-pronouns = Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr(t"bot-profile-render-links = Links"), render_links(profile.links)))
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
        fields.append(CardField(tr(t"bot-profile-render-pronouns = Pronouns"), profile.pronouns))
    if profile.links:
        fields.append(CardField(tr(t"bot-profile-render-links = Links"), render_links(profile.links)))
    if profile.identities:
        fields.append(
            CardField(
                tr(t"bot-profile-render-linked-accounts = Linked accounts"),
                Message.join("\n", (_public_identity_label(identity) for identity in profile.identities)),
            )
        )
    if profile.aliases:
        fields.append(
            CardField(
                tr(t"bot-profile-render-creator-credit = Creator credit"),
                Message.join("\n", (_alias_credit_label(alias.name, alias.build_count) for alias in profile.aliases)),
            )
        )
    return fields


def _public_identity_label(identity: PublicIdentity) -> Message:
    provider = provider_label(identity.provider)
    name = identity.display_name or identity.subject
    return tr(t"bot-profile-render-value = {provider} — {name}")


def _alias_credit_label(name: str, count: int) -> Message:
    return tr(t"""
        bot-profile-render-build = {{ $count ->
            [one] {{ "*" }}*{name}** — {count} build
           *[other] {{ "*" }}*{name}** — {count} builds
        }}
        """)


def present_claimant(claim: AliasClaim, *, mention: bool = True) -> str:
    """Prefer a clickable Discord mention, otherwise use the shared plain label."""
    if mention and claim.claimant is not None:
        discord = claim.claimant.most_recent_identity_for(IdentityProvider.DISCORD)
        if discord is not None and discord.discord_id is not None:
            return f"<@{discord.discord_id}>"
    return claimant_label(claim)

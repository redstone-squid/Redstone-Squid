"""Render identity labels and fields for private and public creator profiles.

Public rendering accepts the account service's already-filtered view. Private
rendering can include the caller's hidden identities and avatar.
"""

from squid.accounts.application.presentation import claimant_label
from squid.accounts.domain import IdentityDetails, IdentityProvider, ProfileLink, PublicCreatorProfile, avatar_url_for
from squid.accounts.models import AccountIdentity, AccountProfile, AliasClaim
from squid.bot.ui import CardField, tr
from squid_i18n import Text, md_tr, text_link
from squid_ui.text import TextLike
from squid_ui.text import mention as text_mention

_PROVIDER_LABELS = {
    IdentityProvider.DISCORD: tr(t"bot-profile-render-discord = Discord"),
    IdentityProvider.JAVA: tr(t"bot-profile-render-minecraft-java = Minecraft (Java)"),
    IdentityProvider.BEDROCK: tr(t"bot-profile-render-minecraft-bedrock = Minecraft (Bedrock)"),
}


def provider_label(provider: IdentityProvider) -> Text:
    """Name a provider the way a player would."""
    return _PROVIDER_LABELS[provider]


def identity_label(identity: AccountIdentity) -> Text:
    """Name one identity for a picker or a list.

    A Discord identity renders as a mention, which is the only handle a reader can click; every
    other provider has a stored display name worth showing, and falls back to the subject.
    """
    provider = provider_label(identity.provider)
    if identity.provider is IdentityProvider.DISCORD and identity.discord_id is not None:
        subject = text_mention(identity.discord_id)
        return md_tr(t"bot-profile-render-value-2 = {provider} — {subject}")
    name = identity.display_name or identity.subject
    return md_tr(t"bot-profile-render-value = {provider} — {name}")


def render_links(links: tuple[ProfileLink, ...]) -> Text:
    """Render links as text links, one per line."""
    return Text.join("\n", (text_link(link.label, link.url) for link in links))


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
                Text.join("\n", (_public_identity_label(identity) for identity in profile.identities)),
            )
        )
    if profile.aliases:
        fields.append(
            CardField(
                tr(t"bot-profile-render-creator-credit = Creator credit"),
                Text.join("\n", (_alias_credit_label(alias.name, alias.build_count) for alias in profile.aliases)),
            )
        )
    return fields


def _public_identity_label(identity: IdentityDetails) -> Text:
    provider = provider_label(identity.provider)
    name = identity.display_name or identity.subject
    return md_tr(t"bot-profile-render-value = {provider} — {name}")


def _alias_credit_label(name: str, count: int) -> Text:
    return md_tr(
        t"""
        bot-profile-render-build = {{ $count ->
            [one] {{ "*" }}*{name}** — {count} build
           *[other] {{ "*" }}*{name}** — {count} builds
        }}
        """,
    )


def present_claimant(claim: AliasClaim, *, mention: bool = True) -> TextLike:
    """Prefer a clickable Discord mention, otherwise use the shared plain label."""
    if mention and claim.claimant is not None:
        discord = claim.claimant.most_recent_identity_for(IdentityProvider.DISCORD)
        if discord is not None and discord.discord_id is not None:
            return text_mention(discord.discord_id)
    return claimant_label(claim)

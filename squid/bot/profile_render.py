"""Rendering for creator profiles in Discord.

Kept out of `verify.py` because the same card is built from two different inputs: the caller's own
profile, which shows hidden fields, and a stranger's, which has already been filtered by
`present_public_profile`. Sharing the field layout here means the two views cannot drift into
describing the same profile differently.
"""

from squid.accounts.domain import (
    AccountIdentity,
    AccountProfile,
    IdentityProvider,
    ProfileLink,
    PublicCreatorProfile,
    avatar_url_for,
)
from squid.bot.i18n import t
from squid.bot.utils.components import CardField
from squid.core.i18n import _

_PROVIDER_LABELS = {
    IdentityProvider.DISCORD: _("Discord"),
    IdentityProvider.JAVA: _("Minecraft (Java)"),
    IdentityProvider.BEDROCK: _("Minecraft (Bedrock)"),
}


def provider_label(provider: IdentityProvider, locale: str) -> str:
    """Name a provider the way a player would."""
    return t(locale, _PROVIDER_LABELS[provider])


def identity_label(identity: AccountIdentity, locale: str) -> str:
    """Name one identity for a picker or a list.

    A Discord identity renders as a mention, which is the only handle a reader can click; every
    other provider has a stored display name worth showing, and falls back to the subject.
    """
    provider = provider_label(identity.provider, locale)
    if identity.provider is IdentityProvider.DISCORD and identity.discord_id is not None:
        return t(locale, _("{provider} — <@{subject}>"), provider=provider, subject=identity.discord_id)
    return t(
        locale,
        _("{provider} — {name}"),
        provider=provider,
        name=identity.display_name or identity.subject,
    )


def render_links(links: tuple[ProfileLink, ...]) -> str:
    """Render links as markdown, one per line."""
    return "\n".join(f"[{link.label}]({link.url})" for link in links)


def own_profile_fields(
    profile: AccountProfile, identities: tuple[AccountIdentity, ...], locale: str
) -> list[CardField]:
    """Describe the caller's own profile, hidden parts included and marked as hidden."""
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(t(locale, _("Pronouns")), profile.pronouns))
    if profile.links:
        fields.append(CardField(t(locale, _("Links")), render_links(profile.links)))
    if identities:
        fields.append(
            CardField(
                t(locale, _("Linked accounts")),
                "\n".join(
                    identity_label(identity, locale) + ("" if identity.is_public else " " + t(locale, _("(hidden)")))
                    for identity in identities
                ),
            )
        )
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


def public_profile_fields(profile: PublicCreatorProfile, locale: str) -> list[CardField]:
    """Describe what a stranger may see.

    Takes an already-filtered `PublicCreatorProfile` rather than filtering here, so the bot and
    the API cannot disagree about visibility.
    """
    fields: list[CardField] = []
    if profile.pronouns:
        fields.append(CardField(t(locale, _("Pronouns")), profile.pronouns))
    if profile.links:
        fields.append(CardField(t(locale, _("Links")), render_links(profile.links)))
    if profile.identities:
        fields.append(
            CardField(
                t(locale, _("Linked accounts")),
                "\n".join(
                    t(
                        locale,
                        _("{provider} — {name}"),
                        provider=provider_label(identity.provider, locale),
                        name=identity.display_name or identity.subject,
                    )
                    for identity in profile.identities
                ),
            )
        )
    if profile.aliases:
        fields.append(
            CardField(
                t(locale, _("Creator credit")),
                "\n".join(
                    t(
                        locale,
                        _("**{name}** — {count} build(s)"),
                        name=alias.name,
                        count=alias.build_count,
                    )
                    for alias in profile.aliases
                ),
            )
        )
    return fields

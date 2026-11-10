"""Plain identity labels shared by account review and suggestion clients."""

from dataclasses import replace

from squid.accounts.domain import IdentityProvider
from squid.accounts.models import AliasClaim
from squid.core.i18n import tr
from squid_i18n import Markup, current_localization, resolve_text


def claimant_label(claim: AliasClaim) -> str:
    """Name a claimant using loaded identities, falling back to diagnostic IDs.

    Prefer a Java display name, then public creator ID, Discord ID, and finally
    internal account ID. Transport adapters may replace the label with a mention.
    """
    account_id = claim.account_id
    message = tr(
        t"accounts-unidentified-account-internal = unidentified account (internal ID {(account_id_text := str(account_id))})"  # noqa: F841
    )
    claimant = claim.claimant
    if claimant is not None:
        java = claimant.most_recent_identity_for(IdentityProvider.JAVA)
        if java is not None and java.display_name is not None:
            return java.display_name
        discord = claimant.most_recent_identity_for(IdentityProvider.DISCORD)
        if claimant.public_creator_id is not None:
            creator_id = str(claimant.public_creator_id)
            message = tr(t"accounts-application-presentation-creator = creator {(creator_id_text := str(creator_id))}")  # noqa: F841 - captured text identifier
        elif discord is not None and discord.discord_id is not None:
            discord_id = discord.discord_id
            message = tr(
                t"accounts-application-presentation-discord-user = Discord user {(discord_id_text := str(discord_id))}"  # noqa: F841
            )
    return resolve_text(replace(message, markup=Markup.PLAIN), current_localization()).content

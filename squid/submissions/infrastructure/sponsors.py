"""Adapters for safe Paper sponsor attribution."""

from uuid import UUID

from squid.minecraft_auth.application import InstallationCredentialService
from squid.sponsors import PublicSponsor


class PaperSponsorResolver:
    """Resolve explicitly published, sponsor-enabled active Paper installations."""

    def __init__(self, installations: InstallationCredentialService) -> None:
        self._installations = installations

    async def resolve(self, installation_id: UUID) -> PublicSponsor | None:
        """Return an allowlisted snapshot or fail closed when public consent is absent."""
        try:
            server = await self._installations.get_public_server(installation_id)
        except ValueError:
            return None
        if server is None or not server.profile.sponsor_opt_in:
            return None
        try:
            return PublicSponsor(
                installation_id=server.installation_id,
                display_name=server.profile.display_name,
                address=server.profile.address,
                description=server.profile.description,
                website_url=server.profile.website_url,
            )
        except ValueError:
            return None

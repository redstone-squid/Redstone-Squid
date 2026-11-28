"""Directory of Paper servers that publish a public profile."""

from datetime import datetime
from uuid import UUID

from litestar import Router, get
from litestar.di import Provide
from litestar.params import FromQuery
from pydantic import AnyHttpUrl, BaseModel, ConfigDict

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import CurrentCaller, MinecraftInstallations, get_minecraft_installations
from squid.api.errors import responses
from squid.api.pagination import CursorParam, ListResponse, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.core.minecraft_auth.domain.models import PublishedPaperServer, ServerListSort


class PublicServerSummary(BaseModel):
    """A server's own public profile; every text field is optional."""

    model_config = ConfigDict(extra="forbid")

    installation_id: UUID
    display_name: str | None
    address: str | None
    description: str | None
    website_url: AnyHttpUrl | None
    sponsor: bool
    """Whether the server also credits itself on builds submitted from it."""
    listed_since: datetime

    @classmethod
    def from_domain(cls, server: PublishedPaperServer) -> PublicServerSummary:
        profile = server.profile
        return cls(
            installation_id=server.installation_id,
            display_name=profile.display_name,
            address=profile.address,
            description=profile.description,
            website_url=profile.website_url,  # pyright: ignore[reportArgumentType]
            sponsor=profile.sponsor_opt_in,
            listed_since=server.created_at.to_stdlib(),
        )


@get(
    "/",
    responses=responses(400, 422, 503),
    response_class=ListResponse,
    operation_id="minecraft_servers_list",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_servers(
    installations: MinecraftInstallations,
    caller: CurrentCaller,
    page_size: PageSizeParam = 50,
    sort: FromQuery[ServerListSort] = ServerListSort.OLDEST,
    cursor: CursorParam = None,
    offset: OffsetParam = None,
) -> Page[PublicServerSummary]:
    """List active Paper servers whose owners turned on their public profile, oldest first."""
    servers = await installations.public_servers(context=caller.context())
    page = sort.page(servers, selector=resolve_selector(offset=offset, cursor=cursor), page_size=page_size)
    return Page[PublicServerSummary].from_domain(page, PublicServerSummary.from_domain)


router = Router(
    dependencies={"installations": Provide(get_minecraft_installations, sync_to_thread=False)},
    path="/minecraft/servers",
    tags=["minecraft-servers"],
    route_handlers=[list_servers],
)

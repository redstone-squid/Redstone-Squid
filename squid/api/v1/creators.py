"""Public creator read routes."""

from typing import Annotated, override
from uuid import UUID

from litestar import Router, get
from litestar.di import Provide
from litestar.dto import DataclassDTO, DTOConfig
from litestar.params import FromPath, PathParameter
from litestar.typing import FieldDefinition

from squid.accounts.domain import PublicCreatorProfile
from squid.accounts.errors import CreatorAliasNotFoundError, CreatorNotFoundError
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Accounts, get_accounts
from squid.api.errors import responses
from squid.api.v1.schemas.creators import CreatorAliasDetail


class PublicCreatorDTO(DataclassDTO[PublicCreatorProfile]):
    """Expose the service's visibility-filtered profile with the public API's field names."""

    __schema_name__ = "CreatorProfileDetail"

    config = DTOConfig(
        rename_fields={"public_id": "id", "canonical_public_id": "canonical_id"},
        include={
            "public_id",
            "canonical_public_id",
            "hidden",
            "display_name",
            "bio",
            "pronouns",
            "avatar_url",
            "joined_at",
            "aliases",
            "links",
            "identities",
        },
    )

    @classmethod
    @override
    def detect_nested_field(cls, field_definition: FieldDefinition) -> bool:
        # These service-owned leaf dataclasses already contain only public fields. Keeping
        # them native also avoids Litestar's process-global nested DTO schema-name counter.
        return False


@get(
    "/{name:str}",
    responses=responses(404, 422, 503),
    operation_id="creator_alias_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_creator_alias(
    name: Annotated[str, PathParameter(min_length=1, max_length=64)], accounts: Accounts
) -> CreatorAliasDetail:
    """Return a creator credit without exposing its linked account."""
    alias = await accounts.get_creator_alias(name)
    if alias is None:
        raise CreatorAliasNotFoundError(name)
    return CreatorAliasDetail.from_domain(alias)


@get(
    "/{creator_id:uuid}",
    responses=responses(404, 422, 503),
    operation_id="creator_profile_get",
    return_dto=PublicCreatorDTO,
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_creator_profile(creator_id: FromPath[UUID], accounts: Accounts) -> PublicCreatorProfile:
    """Return a creator's public profile, following account-merge redirects.

    The account service applies profile and identity visibility before this route
    serializes the result.
    """
    profile = await accounts.get_public_profile(creator_id)
    if profile is None:
        raise CreatorNotFoundError(creator_id)
    return profile


router = Router(
    dependencies={"accounts": Provide(get_accounts, sync_to_thread=False)},
    path="/creator-aliases",
    tags=["creators"],
    route_handlers=[get_creator_alias],
)
profiles_router = Router(
    dependencies={"accounts": Provide(get_accounts, sync_to_thread=False)},
    path="/creators",
    tags=["creators"],
    route_handlers=[get_creator_profile],
)

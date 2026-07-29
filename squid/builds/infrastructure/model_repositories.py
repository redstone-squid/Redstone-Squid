"""Advanced Alchemy bindings for build-owned models."""

from squid.builds.infrastructure.models import Restriction, RestrictionAlias, Type
from squid.persistence.repository import BaseAsyncRepository


class RestrictionModelRepository(BaseAsyncRepository[Restriction]):
    model_type = Restriction


class RestrictionAliasModelRepository(BaseAsyncRepository[RestrictionAlias]):
    model_type = RestrictionAlias


class TypeModelRepository(BaseAsyncRepository[Type]):
    model_type = Type

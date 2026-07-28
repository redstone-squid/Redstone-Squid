"""Shared advanced-alchemy model-repository bindings.

These are trivial `BaseAsyncRepository[Model]` subclasses that only bind
`model_type`. They are collected here because several of them are used by
more than one public repository (e.g. messages are read by both
`MessageRepository` and `VoteRepository`), so there is no single obvious
owning file for them.
"""

from squid.db.repos._base import BaseAsyncRepository
from squid.db.schema import (
    Message,
    Restriction,
    RestrictionAlias,
    ServerSetting,
    Type,
    User,
    Version,
    Vote,
    VoteSession,
)
from squid.db.schema import VerificationCode as VerificationCodeModel


class MessageModelRepository(BaseAsyncRepository[Message]):
    model_type = Message


class RestrictionModelRepository(BaseAsyncRepository[Restriction]):
    model_type = Restriction


class RestrictionAliasModelRepository(BaseAsyncRepository[RestrictionAlias]):
    model_type = RestrictionAlias


class ServerSettingModelRepository(BaseAsyncRepository[ServerSetting]):
    model_type = ServerSetting


class TypeModelRepository(BaseAsyncRepository[Type]):
    model_type = Type


class UserModelRepository(BaseAsyncRepository[User]):
    model_type = User


class VerificationCodeModelRepository(BaseAsyncRepository[VerificationCodeModel]):
    model_type = VerificationCodeModel


class VersionModelRepository(BaseAsyncRepository[Version]):
    model_type = Version


class VoteModelRepository(BaseAsyncRepository[Vote]):
    model_type = Vote


class VoteSessionModelRepository(BaseAsyncRepository[VoteSession]):
    model_type = VoteSession

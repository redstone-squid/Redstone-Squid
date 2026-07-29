"""Shared advanced-alchemy model-repository bindings.

These are trivial `BaseAsyncRepository[Model]` subclasses that only bind
`model_type`. They are collected here because several of them are used by
more than one public repository (e.g. messages are read by both
`MessageRepository` and `VoteRepository`), so there is no single obvious
owning file for them.
"""

from squid.builds.infrastructure.models import Restriction, RestrictionAlias, Type
from squid.messages.infrastructure.models import Message
from squid.persistence.repository import BaseAsyncRepository
from squid.settings.infrastructure.models import ServerSetting
from squid.users.infrastructure.models import User
from squid.users.infrastructure.models import VerificationCode as VerificationCodeModel
from squid.versions.infrastructure.models import Version
from squid.voting.infrastructure.models import Vote, VoteSession


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
